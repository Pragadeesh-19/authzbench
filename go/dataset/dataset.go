package dataset

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

const (
	Format            = "authzbench.dataset/1"
	manifestFile      = "dataset.json"
	relationshipsFile = "relationships.txt"
	checksFile        = "checks.txt"
)

type Revocation struct {
	Revoke Relationship
	Probe  Query
}

type Dataset struct {
	Model         string
	Scale         string
	Seed          int64
	Relationships []Relationship
	Expectations  []Expectation
	Revocation    Revocation
	Nesting       Expectation
}

type manifest struct {
	Format        string `json:"format"`
	Model         string `json:"model"`
	Scale         string `json:"scale"`
	Seed          int64  `json:"seed"`
	Relationships int    `json:"relationships"`
	Checks        int    `json:"checks"`
	Revocation    struct {
		Revoke string `json:"revoke"`
		Probe  string `json:"probe"`
	} `json:"revocation"`
	Nesting string `json:"nesting"`
}

func Read(directory string) (*Dataset, error) {
	described, err := readManifest(filepath.Join(directory, manifestFile))
	if err != nil {
		return nil, err
	}
	relationships, err := readLines(filepath.Join(directory, relationshipsFile), ParseRelationship)
	if err != nil {
		return nil, err
	}
	expectations, err := readLines(filepath.Join(directory, checksFile), ParseExpectation)
	if err != nil {
		return nil, err
	}
	if err := requireCount("relationships", described.Relationships, len(relationships)); err != nil {
		return nil, err
	}
	if err := requireCount("checks", described.Checks, len(expectations)); err != nil {
		return nil, err
	}
	return assemble(described, relationships, expectations)
}

func readManifest(path string) (manifest, error) {
	var described manifest
	content, err := os.ReadFile(path)
	if err != nil {
		return described, err
	}
	if err := json.Unmarshal(content, &described); err != nil {
		return described, fmt.Errorf("%s: %w", path, err)
	}
	if described.Format != Format {
		return described, fmt.Errorf("%s is not an %s manifest", path, Format)
	}
	return described, nil
}

func assemble(described manifest, relationships []Relationship, expectations []Expectation) (*Dataset, error) {
	revoke, err := ParseRelationship(described.Revocation.Revoke)
	if err != nil {
		return nil, err
	}
	probe, err := ParseQuery(described.Revocation.Probe)
	if err != nil {
		return nil, err
	}
	nesting, err := ParseExpectation(described.Nesting)
	if err != nil {
		return nil, err
	}
	return &Dataset{
		Model:         described.Model,
		Scale:         described.Scale,
		Seed:          described.Seed,
		Relationships: relationships,
		Expectations:  expectations,
		Revocation:    Revocation{Revoke: revoke, Probe: probe},
		Nesting:       nesting,
	}, nil
}

func readLines[T any](path string, parse func(string) (T, error)) ([]T, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	var items []T
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		if scanner.Text() == "" {
			continue
		}
		item, err := parse(scanner.Text())
		if err != nil {
			return nil, fmt.Errorf("%s: %w", path, err)
		}
		items = append(items, item)
	}
	return items, scanner.Err()
}

func requireCount(kind string, promised, found int) error {
	if promised != found {
		return fmt.Errorf("the manifest promises %d %s but the file holds %d", promised, kind, found)
	}
	return nil
}
