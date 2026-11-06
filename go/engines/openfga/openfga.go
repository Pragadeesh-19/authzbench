package openfga

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"

	fga "github.com/openfga/go-sdk"
	"github.com/openfga/go-sdk/client"
	"github.com/openfga/language/pkg/go/transformer"

	"github.com/Pragadeesh-19/authzbench/go/dataset"
	"github.com/Pragadeesh-19/authzbench/go/engine"
)

const tuplesPerWrite = 100

var consistencies = map[engine.Consistency]fga.ConsistencyPreference{
	engine.Fresh:   fga.CONSISTENCYPREFERENCE_HIGHER_CONSISTENCY,
	engine.Fastest: fga.CONSISTENCYPREFERENCE_MINIMIZE_LATENCY,
}

type Engine struct {
	client  *client.OpenFgaClient
	model   fga.WriteAuthorizationModelRequest
	version string
}

func Dial(apiURL, modelDSL, version string, connections int) (*Engine, error) {
	model, err := parseModel(modelDSL)
	if err != nil {
		return nil, err
	}
	sdk, err := client.NewSdkClient(&client.ClientConfiguration{ApiUrl: apiURL, HTTPClient: pooledClient(connections)})
	if err != nil {
		return nil, err
	}
	return &Engine{client: sdk, model: model, version: version}, nil
}

func (e *Engine) Name() string    { return "openfga" }
func (e *Engine) Version() string { return e.version }

func (e *Engine) Modes() []engine.Mode {
	return []engine.Mode{
		{Consistency: engine.Fresh, Name: string(consistencies[engine.Fresh])},
		{Consistency: engine.Fastest, Name: string(consistencies[engine.Fastest])},
	}
}

func (e *Engine) Load(ctx context.Context, data *dataset.Dataset) error {
	name := fmt.Sprintf("authzbench-%s-%s-%d", data.Model, data.Scale, data.Seed)
	store, err := e.client.CreateStore(ctx).Body(client.ClientCreateStoreRequest{Name: name}).Execute()
	if err != nil {
		return err
	}
	if err := e.client.SetStoreId(store.Id); err != nil {
		return err
	}
	written, err := e.client.WriteAuthorizationModel(ctx).Body(e.model).Execute()
	if err != nil {
		return err
	}
	if err := e.client.SetAuthorizationModelId(written.AuthorizationModelId); err != nil {
		return err
	}
	for start := 0; start < len(data.Relationships); start += tuplesPerWrite {
		batch := data.Relationships[start:min(start+tuplesPerWrite, len(data.Relationships))]
		tuples := make([]client.ClientTupleKey, len(batch))
		for index, relationship := range batch {
			tuples[index] = tuple(relationship)
		}
		if _, err := e.client.Write(ctx).Body(client.ClientWriteRequest{Writes: tuples}).Execute(); err != nil {
			return err
		}
	}
	return nil
}

func (e *Engine) Write(ctx context.Context, change engine.Change) (engine.Revision, error) {
	written := tuple(change.Relationship)
	request := client.ClientWriteRequest{}
	if change.Operation == engine.Add {
		request.Writes = []client.ClientTupleKey{written}
	} else {
		request.Deletes = []client.ClientTupleKeyWithoutCondition{
			{User: written.User, Relation: written.Relation, Object: written.Object},
		}
	}
	_, err := e.client.Write(ctx).Body(request).Execute()
	return nil, err
}

func (e *Engine) Check(ctx context.Context, query dataset.Query, consistency engine.Consistency, _ engine.Revision) (bool, error) {
	preference := consistencies[consistency]
	response, err := e.client.Check(ctx).
		Body(client.ClientCheckRequest{
			User:     query.Subject.String(),
			Relation: query.Permission,
			Object:   query.Resource.String(),
		}).
		Options(client.ClientCheckOptions{Consistency: &preference}).
		Execute()
	if err != nil {
		return false, err
	}
	return response.GetAllowed(), nil
}

func (e *Engine) Close() error {
	return nil
}

func pooledClient(connections int) *http.Client {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.MaxIdleConns = connections
	transport.MaxIdleConnsPerHost = connections
	return &http.Client{Transport: transport}
}

func parseModel(modelDSL string) (fga.WriteAuthorizationModelRequest, error) {
	var model fga.WriteAuthorizationModelRequest
	modelJSON, err := transformer.TransformDSLToJSON(modelDSL)
	if err != nil {
		return model, err
	}
	err = json.Unmarshal([]byte(modelJSON), &model)
	return model, err
}

func tuple(relationship dataset.Relationship) client.ClientTupleKey {
	return client.ClientTupleKey{
		User:     relationship.Subject.String(),
		Relation: relationship.Relation,
		Object:   relationship.Resource.String(),
	}
}
