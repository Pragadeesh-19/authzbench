package spicedb

import (
	"context"
	"fmt"

	"github.com/authzed/authzed-go/pkg/requestmeta"
	"github.com/authzed/authzed-go/pkg/responsemeta"
	v1 "github.com/authzed/authzed-go/proto/authzed/api/v1"
	authzed "github.com/authzed/authzed-go/v1"
	"github.com/authzed/grpcutil"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"

	"github.com/Pragadeesh-19/authzbench/go/dataset"
	"github.com/Pragadeesh-19/authzbench/go/engine"
)

const (
	updatesPerWrite  = 1000
	deletesPerDelete = 1000
)

var (
	consistencies = map[engine.Consistency]*v1.Consistency{
		engine.Fresh:   {Requirement: &v1.Consistency_FullyConsistent{FullyConsistent: true}},
		engine.Fastest: {Requirement: &v1.Consistency_MinimizeLatency{MinimizeLatency: true}},
	}
	operations = map[engine.Operation]v1.RelationshipUpdate_Operation{
		engine.Add:    v1.RelationshipUpdate_OPERATION_TOUCH,
		engine.Remove: v1.RelationshipUpdate_OPERATION_DELETE,
	}
)

type Engine struct {
	client  *authzed.Client
	schema  string
	version string
}

func Dial(endpoint, presharedKey, schema string) (*Engine, error) {
	client, err := authzed.NewClient(endpoint,
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpcutil.WithInsecureBearerToken(presharedKey))
	if err != nil {
		return nil, err
	}
	return &Engine{client: client, schema: schema}, nil
}

func (e *Engine) Name() string    { return "spicedb" }
func (e *Engine) Version() string { return e.version }

func (e *Engine) Modes() []engine.Mode {
	return []engine.Mode{
		{Consistency: engine.Fresh, Name: "fully_consistent"},
		{Consistency: engine.Fastest, Name: "minimize_latency"},
	}
}

func (e *Engine) Load(ctx context.Context, data *dataset.Dataset) error {
	if err := e.writeSchema(ctx); err != nil {
		return err
	}
	for resourceType := range resourceTypes(data.Relationships) {
		if err := e.deleteAll(ctx, resourceType); err != nil {
			return err
		}
	}
	for start := 0; start < len(data.Relationships); start += updatesPerWrite {
		batch := data.Relationships[start:min(start+updatesPerWrite, len(data.Relationships))]
		updates := make([]*v1.RelationshipUpdate, len(batch))
		for index, relationship := range batch {
			updates[index] = update(engine.Add, relationship)
		}
		if _, err := e.client.WriteRelationships(ctx, &v1.WriteRelationshipsRequest{Updates: updates}); err != nil {
			return err
		}
	}
	return nil
}

func (e *Engine) Write(ctx context.Context, change engine.Change) (engine.Revision, error) {
	response, err := e.client.WriteRelationships(ctx, &v1.WriteRelationshipsRequest{
		Updates: []*v1.RelationshipUpdate{update(change.Operation, change.Relationship)},
	})
	if err != nil {
		return nil, err
	}
	return response.WrittenAt, nil
}

func (e *Engine) Check(ctx context.Context, query dataset.Query, consistency engine.Consistency, _ engine.Revision) (bool, error) {
	response, err := e.client.CheckPermission(ctx, &v1.CheckPermissionRequest{
		Consistency: consistencies[consistency],
		Resource:    object(query.Resource),
		Permission:  query.Permission,
		Subject:     &v1.SubjectReference{Object: object(query.Subject)},
	})
	if err != nil {
		return false, err
	}
	switch response.Permissionship {
	case v1.CheckPermissionResponse_PERMISSIONSHIP_HAS_PERMISSION:
		return true, nil
	case v1.CheckPermissionResponse_PERMISSIONSHIP_NO_PERMISSION:
		return false, nil
	default:
		return false, fmt.Errorf("%s: unexpected permissionship %s", query, response.Permissionship)
	}
}

func (e *Engine) Close() error {
	return e.client.Close()
}

func (e *Engine) writeSchema(ctx context.Context) error {
	var trailer metadata.MD
	ctx = requestmeta.AddRequestHeaders(ctx, requestmeta.RequestServerVersion)
	if _, err := e.client.WriteSchema(ctx, &v1.WriteSchemaRequest{Schema: e.schema}, grpc.Trailer(&trailer)); err != nil {
		return err
	}
	versions := trailer.Get(string(responsemeta.ServerVersion))
	if len(versions) == 0 {
		return fmt.Errorf("SpiceDB did not report its version")
	}
	e.version = versions[0]
	return nil
}

func (e *Engine) deleteAll(ctx context.Context, resourceType string) error {
	for {
		response, err := e.client.DeleteRelationships(ctx, &v1.DeleteRelationshipsRequest{
			RelationshipFilter:            &v1.RelationshipFilter{ResourceType: resourceType},
			OptionalLimit:                 deletesPerDelete,
			OptionalAllowPartialDeletions: true,
		})
		if err != nil {
			return err
		}
		if response.DeletionProgress == v1.DeleteRelationshipsResponse_DELETION_PROGRESS_COMPLETE {
			return nil
		}
	}
}

func resourceTypes(relationships []dataset.Relationship) map[string]struct{} {
	types := make(map[string]struct{})
	for _, relationship := range relationships {
		types[relationship.Resource.Type] = struct{}{}
	}
	return types
}

func update(operation engine.Operation, relationship dataset.Relationship) *v1.RelationshipUpdate {
	return &v1.RelationshipUpdate{
		Operation: operations[operation],
		Relationship: &v1.Relationship{
			Resource: object(relationship.Resource),
			Relation: relationship.Relation,
			Subject: &v1.SubjectReference{
				Object:           object(relationship.Subject.Object),
				OptionalRelation: relationship.Subject.Relation,
			},
		},
	}
}

func object(reference dataset.Object) *v1.ObjectReference {
	return &v1.ObjectReference{ObjectType: reference.Type, ObjectId: reference.ID}
}
