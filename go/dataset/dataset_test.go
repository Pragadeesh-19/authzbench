package dataset

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRelationshipsSurviveARoundTrip(t *testing.T) {
	for _, line := range []string{
		"document:d1#viewer@user:u1",
		"document:d1#viewer@group:g1#member",
		"folder:revocation-root#parent@folder:F_9",
	} {
		relationship, err := ParseRelationship(line)
		if err != nil || relationship.String() != line {
			t.Errorf("line=%q got %q, %v", line, relationship, err)
		}
	}
}

func TestChecksSurviveARoundTrip(t *testing.T) {
	for _, line := range []string{"document:d1#view@user:u1 allow", "document:d1#view@user:u1 deny"} {
		expectation, err := ParseExpectation(line)
		if err != nil || expectation.String() != line {
			t.Errorf("line=%q got %q, %v", line, expectation, err)
		}
	}
}

func TestMalformedLinesAreRefused(t *testing.T) {
	for _, line := range []string{"", "document:d1#viewer", "Document:d1#viewer@user:u1", "document:d 1#viewer@user:u1"} {
		if _, err := ParseRelationship(line); err == nil {
			t.Errorf("relationship line=%q was accepted", line)
		}
	}
	for _, line := range []string{"document:d1#view@user:u1", "document:d1#view@user:u1 maybe",
		"document:d1#view@group:g1#member allow"} {
		if _, err := ParseExpectation(line); err == nil {
			t.Errorf("check line=%q was accepted", line)
		}
	}
}

func TestADatasetIsReadWithItsScenarios(t *testing.T) {
	data, err := Read(filepath.Join("testdata", "tiny"))
	if err != nil {
		t.Fatal(err)
	}
	if len(data.Relationships) != 5 || len(data.Expectations) != 2 {
		t.Fatalf("read %d relationships and %d checks", len(data.Relationships), len(data.Expectations))
	}
	if data.Revocation.Revoke.String() != "group:revocation-team#member@user:revocation-target" {
		t.Errorf("revoke = %s", data.Revocation.Revoke)
	}
	if data.Revocation.Probe.String() != "document:revocation-target#view@user:revocation-target" {
		t.Errorf("probe = %s", data.Revocation.Probe)
	}
	if !data.Nesting.Allowed || data.Model != "document-sharing" || data.Seed != 1 {
		t.Errorf("manifest fields: %+v", data)
	}
}

func TestATruncatedFileIsRefused(t *testing.T) {
	directory := t.TempDir()
	for _, name := range []string{manifestFile, relationshipsFile, checksFile} {
		content, err := os.ReadFile(filepath.Join("testdata", "tiny", name))
		if err != nil {
			t.Fatal(err)
		}
		if name == checksFile {
			content = []byte(strings.SplitAfterN(string(content), "\n", 2)[0])
		}
		if err := os.WriteFile(filepath.Join(directory, name), content, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := Read(directory); err == nil || !strings.Contains(err.Error(), "promises 2 checks") {
		t.Fatalf("err = %v", err)
	}
}
