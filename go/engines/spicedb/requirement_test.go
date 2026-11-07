package spicedb

import (
	"testing"

	v1 "github.com/authzed/authzed-go/proto/authzed/api/v1"

	"github.com/Pragadeesh-19/authzbench/go/engine"
)

func TestAFreshCheckReadsAsTheEngineIsConfigured(t *testing.T) {
	latest, revoke := &v1.ZedToken{Token: "latest"}, &v1.ZedToken{Token: "revoke"}
	tokened := &Engine{freshRead: AtLeastAsFresh}
	tokened.latest.Store(latest)
	cases := []struct {
		name        string
		target      *Engine
		consistency engine.Consistency
		after       engine.Revision
		want        *v1.Consistency
	}{
		{"fully consistent", &Engine{freshRead: FullyConsistent}, engine.Fresh, revoke, fullyConsistent},
		{"fastest ignores the token", tokened, engine.Fastest, revoke, minimizeLatency},
		{"the race's token", tokened, engine.Fresh, revoke, atLeastAsFresh(revoke)},
		{"the latest write's token", tokened, engine.Fresh, nil, atLeastAsFresh(latest)},
	}
	for _, c := range cases {
		got, err := c.target.requirement(c.consistency, c.after)
		if err != nil || got.String() != c.want.String() {
			t.Errorf("%s: requirement = %v, %v; want %v", c.name, got, err, c.want)
		}
	}
}

func TestATokenedCheckBeforeAnyWriteIsRefused(t *testing.T) {
	if _, err := (&Engine{freshRead: AtLeastAsFresh}).requirement(engine.Fresh, nil); err == nil {
		t.Fatal("a check with no token to be as fresh as was answered")
	}
}

func TestAnUnknownConsistencyIsRefused(t *testing.T) {
	if _, err := (&Engine{freshRead: FullyConsistent}).requirement("eventually", nil); err == nil {
		t.Fatal("an unknown consistency was answered")
	}
}

func atLeastAsFresh(token *v1.ZedToken) *v1.Consistency {
	return &v1.Consistency{Requirement: &v1.Consistency_AtLeastAsFresh{AtLeastAsFresh: token}}
}
