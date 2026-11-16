package webserver

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"sync"

	"github.com/jech/galene/group"
)

// Branding holds the customisable appearance of the platform.  It is
// stored as JSON in the data directory and served to the client so the
// UI can apply it without a rebuild.
type Branding struct {
	SiteName     string `json:"siteName"`
	Tagline      string `json:"tagline"`
	LogoURL      string `json:"logoURL"`
	FaviconURL   string `json:"faviconURL"`
	PrimaryColor string `json:"primaryColor"`
}

var defaultBranding = Branding{
	SiteName:     "Galène",
	PrimaryColor: "#2e7d32",
}

var brandingState struct {
	mu       sync.Mutex
	branding Branding
	loaded   bool
}

func brandingFile() string {
	return filepath.Join(group.DataDirectory, "branding.json")
}

// GetBranding returns the current branding, loading it from disk on
// first use.
func GetBranding() Branding {
	brandingState.mu.Lock()
	defer brandingState.mu.Unlock()

	if !brandingState.loaded {
		brandingState.branding = defaultBranding
		data, err := os.ReadFile(brandingFile())
		if err == nil && len(data) > 0 {
			var b Branding
			if err := json.Unmarshal(data, &b); err == nil {
				brandingState.branding = b
			}
		}
		brandingState.loaded = true
	}
	return brandingState.branding
}

// SetBranding persists new branding settings.
func SetBranding(b Branding) error {
	brandingState.mu.Lock()
	defer brandingState.mu.Unlock()

	data, err := json.Marshal(b)
	if err != nil {
		return err
	}
	if err := os.WriteFile(brandingFile(), data, 0600); err != nil {
		return err
	}
	brandingState.branding = b
	brandingState.loaded = true
	return nil
}

// brandingHandler serves the public branding configuration.
func brandingHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != "GET" && r.Method != "HEAD" {
		methodNotAllowed(w, "GET, HEAD")
		return
	}
	w.Header().Set("content-type", "application/json")
	w.Header().Set("cache-control", "no-cache")
	if r.Method == "HEAD" {
		return
	}
	_ = json.NewEncoder(w).Encode(GetBranding())
}

// brandingAdminHandler lets an administrator read and update branding.
func brandingAdminHandler(w http.ResponseWriter, r *http.Request) {
	if apiCORS(w, r, "GET, PUT") {
		return
	}
	if !checkAdmin(w, r, "") {
		return
	}
	if r.Method == "GET" || r.Method == "HEAD" {
		sendJSON(w, r, GetBranding())
		return
	}
	if r.Method == "PUT" {
		var b Branding
		if getJSON(w, r, &b) {
			return
		}
		if err := SetBranding(b); err != nil {
			log.Printf("SetBranding: %v", err)
			httpError(w, err)
			return
		}
		w.WriteHeader(http.StatusNoContent)
		return
	}
	methodNotAllowed(w, "GET, PUT")
}
