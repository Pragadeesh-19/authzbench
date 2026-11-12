package dataset

import (
	"fmt"
	"regexp"
	"strings"
)

const (
	namePattern = `[a-z][a-z0-9_]*`
	idPattern   = `[A-Za-z0-9_\-]+`
)

func objectPattern(role string) string {
	return fmt.Sprintf(`(?P<%s_type>%s):(?P<%s_id>%s)`, role, namePattern, role, idPattern)
}

var (
	relationshipPattern = regexp.MustCompile(`^` + objectPattern("resource") + `#(?P<relation>` + namePattern + `)@` +
		objectPattern("subject") + `(?:#(?P<subject_relation>` + namePattern + `))?$`)
	objectOnlyPattern = regexp.MustCompile(`^` + objectPattern("object") + `$`)
	queryPattern      = regexp.MustCompile(`^` + objectPattern("resource") + `#(?P<permission>` + namePattern + `)@` +
		objectPattern("subject") + `$`)
	answers = map[string]bool{"allow": true, "deny": false}
)

type Object struct {
	Type string
	ID   string
}

func (o Object) String() string { return o.Type + ":" + o.ID }

type Subject struct {
	Object   Object
	Relation string
}

func (s Subject) String() string {
	if s.Relation == "" {
		return s.Object.String()
	}
	return s.Object.String() + "#" + s.Relation
}

type Relationship struct {
	Resource Object
	Relation string
	Subject  Subject
}

func (r Relationship) String() string {
	return r.Resource.String() + "#" + r.Relation + "@" + r.Subject.String()
}

type Query struct {
	Resource   Object
	Permission string
	Subject    Object
}

func (q Query) String() string {
	return q.Resource.String() + "#" + q.Permission + "@" + q.Subject.String()
}

type Expectation struct {
	Query   Query
	Allowed bool
}

func (e Expectation) String() string {
	if e.Allowed {
		return e.Query.String() + " allow"
	}
	return e.Query.String() + " deny"
}

func ParseRelationship(line string) (Relationship, error) {
	groups, ok := match(relationshipPattern, line)
	if !ok {
		return Relationship{}, fmt.Errorf("not a relationship: %q", line)
	}
	subject := Subject{Object: object(groups, "subject"), Relation: groups["subject_relation"]}
	return Relationship{Resource: object(groups, "resource"), Relation: groups["relation"], Subject: subject}, nil
}

func ParseQuery(text string) (Query, error) {
	groups, ok := match(queryPattern, text)
	if !ok {
		return Query{}, fmt.Errorf("not a check: %q", text)
	}
	return Query{Resource: object(groups, "resource"), Permission: groups["permission"], Subject: object(groups, "subject")}, nil
}

func ParseExpectation(line string) (Expectation, error) {
	trimmed := strings.TrimSpace(line)
	separator := strings.LastIndexByte(trimmed, ' ')
	if separator < 0 {
		return Expectation{}, fmt.Errorf("a check ends with allow or deny: %q", line)
	}
	allowed, known := answers[trimmed[separator+1:]]
	if !known {
		return Expectation{}, fmt.Errorf("a check ends with allow or deny: %q", line)
	}
	query, err := ParseQuery(trimmed[:separator])
	if err != nil {
		return Expectation{}, err
	}
	return Expectation{Query: query, Allowed: allowed}, nil
}

func ParseObject(text string) (Object, error) {
	groups, ok := match(objectOnlyPattern, text)
	if !ok {
		return Object{}, fmt.Errorf("not an object: %q", text)
	}
	return object(groups, "object"), nil
}

func match(pattern *regexp.Regexp, text string) (map[string]string, bool) {
	values := pattern.FindStringSubmatch(strings.TrimSpace(text))
	if values == nil {
		return nil, false
	}
	groups := make(map[string]string, len(values))
	for index, name := range pattern.SubexpNames() {
		if name != "" {
			groups[name] = values[index]
		}
	}
	return groups, true
}

func object(groups map[string]string, role string) Object {
	return Object{Type: groups[role+"_type"], ID: groups[role+"_id"]}
}
