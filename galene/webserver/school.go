package webserver

import (
	"net/http"

	"github.com/jech/galene/school"
)

// schoolHandler exposes the school synchronisation to administrators.
//
//	GET  /galene-api/v0/.school/status   state of the last sync
//	POST /galene-api/v0/.school/sync     run a sync now
func schoolHandler(w http.ResponseWriter, r *http.Request, rest string) {
	if apiCORS(w, r, "GET, POST") {
		return
	}
	if !checkAdmin(w, r, "") {
		return
	}

	switch rest {
	case "/status", "":
		if r.Method != "GET" && r.Method != "HEAD" {
			methodNotAllowed(w, "GET")
			return
		}
		sendJSON(w, r, school.GetStatus())
		return
	case "/sync":
		if r.Method != "POST" {
			methodNotAllowed(w, "POST")
			return
		}
		res, err := school.Sync()
		if err != nil {
			httpError(w, err)
			return
		}
		sendJSON(w, r, res)
		return
	}

	http.NotFound(w, r)
}
