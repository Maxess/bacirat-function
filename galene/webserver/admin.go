package webserver

import (
	"errors"
	"net/http"
	"os"
	"strings"

	"github.com/jech/galene/group"
)

// attendanceHandler serves attendance records for a group.
// GET /galene-api/v0/.attendance/{group}?date=YYYY-MM-DD
// GET /galene-api/v0/.attendance/{group}/.dates
func attendanceHandler(w http.ResponseWriter, r *http.Request, rest string) {
	if apiCORS(w, r, "GET") {
		return
	}
	if !checkAdmin(w, r, "") {
		return
	}
	if r.Method != "GET" && r.Method != "HEAD" {
		methodNotAllowed(w, "GET")
		return
	}

	// rest is "/{group}" or "/{group}/.dates"
	parts := strings.Split(strings.Trim(rest, "/"), "/")
	if len(parts) == 0 || parts[0] == "" {
		http.NotFound(w, r)
		return
	}
	g := parts[0]

	if len(parts) == 2 && parts[1] == ".dates" {
		dates, err := group.GetAttendanceDates(g)
		if err != nil {
			if errors.Is(err, os.ErrNotExist) {
				sendJSON(w, r, []string{})
				return
			}
			httpError(w, err)
			return
		}
		sendJSON(w, r, dates)
		return
	}

	if len(parts) != 1 {
		http.NotFound(w, r)
		return
	}

	date := r.URL.Query().Get("date")
	records, err := group.GetAttendance(g, date)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			sendJSON(w, r, []group.AttendanceRecord{})
			return
		}
		httpError(w, err)
		return
	}
	sendJSON(w, r, records)
}

// whiteboardAdminHandler returns or clears the current whiteboard state
// of a group.
//
//	GET    /galene-api/v0/.whiteboard/{group}
//	DELETE /galene-api/v0/.whiteboard/{group}
func whiteboardAdminHandler(w http.ResponseWriter, r *http.Request, rest string) {
	if apiCORS(w, r, "GET, DELETE") {
		return
	}
	if !checkAdmin(w, r, "") {
		return
	}
	if r.Method != "GET" && r.Method != "HEAD" && r.Method != "DELETE" {
		methodNotAllowed(w, "GET, DELETE")
		return
	}

	g := strings.Trim(rest, "/")
	if g == "" {
		http.NotFound(w, r)
		return
	}

	grp := group.Get(g)
	if grp == nil {
		// The group isn't loaded, so there is nothing to clear or to
		// show.  Confirm that the group does exist, though, so that a
		// typo doesn't silently succeed.
		if _, err := group.GetDescription(g); err != nil {
			httpError(w, err)
			return
		}
		if r.Method == "DELETE" {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		sendJSON(w, r, []group.WhiteboardStroke{})
		return
	}
	if r.Method == "DELETE" {
		grp.ClearWhiteboard()
		w.WriteHeader(http.StatusNoContent)
		return
	}
	sendJSON(w, r, grp.GetWhiteboard())
}

// websiteAPIHandler provides a small, website-friendly API for syncing
// classes (groups) and students (users).  It reuses the administrator
// authentication of the Galene API.
//
//	GET  /api/v1/groups                  list classes
//	POST /api/v1/groups/{name}/sync      create/update a class + students
type websiteSyncRequest struct {
	DisplayName string            `json:"displayName"`
	Description string            `json:"description"`
	Users       []websiteSyncUser `json:"users"`
}

type websiteSyncUser struct {
	Username    string `json:"username"`
	Password    string `json:"password,omitempty"`
	Permissions string `json:"permissions"`
}

func websiteAPIHandler(w http.ResponseWriter, r *http.Request) {
	if !strings.HasPrefix(r.URL.Path, "/api/v1/") {
		http.NotFound(w, r)
		return
	}
	rest := strings.TrimPrefix(r.URL.Path, "/api/v1/")
	parts := strings.Split(strings.Trim(rest, "/"), "/")

	if len(parts) == 1 && parts[0] == "groups" {
		if apiCORS(w, r, "GET") {
			return
		}
		if !checkAdmin(w, r, "") {
			return
		}
		if r.Method != "GET" && r.Method != "HEAD" {
			methodNotAllowed(w, "GET")
			return
		}
		groups, err := group.GetDescriptionNames()
		if err != nil {
			httpError(w, err)
			return
		}
		sendJSON(w, r, groups)
		return
	}

	if len(parts) == 3 && parts[0] == "groups" && parts[2] == "sync" {
		if apiCORS(w, r, "POST") {
			return
		}
		if !checkAdmin(w, r, "") {
			return
		}
		if r.Method != "POST" {
			methodNotAllowed(w, "POST")
			return
		}
		g := parts[1]

		var req websiteSyncRequest
		if getJSON(w, r, &req) {
			return
		}

		// create or update the group description
		etag, err := group.GetDescriptionTag(g)
		if errors.Is(err, os.ErrNotExist) {
			etag = ""
			err = nil
		}
		if err != nil {
			httpError(w, err)
			return
		}

		desc := &group.Description{
			DisplayName: req.DisplayName,
			Description: req.Description,
			Public:      false,
		}
		if err := group.UpdateDescription(g, etag, desc); err != nil {
			httpError(w, err)
			return
		}

		// upsert users
		for _, u := range req.Users {
			if u.Username == "" {
				continue
			}
			perms, err := group.NewPermissions(u.Permissions)
			if err != nil {
				perms, _ = group.NewPermissions("student")
			}
			uetag, err := group.GetUserTag(g, u.Username, false)
			if errors.Is(err, os.ErrNotExist) {
				uetag = ""
				err = nil
			}
			if err != nil {
				httpError(w, err)
				return
			}
			udesc := &group.UserDescription{Permissions: perms}
			if err := group.UpdateUser(g, u.Username, false, uetag, udesc); err != nil {
				httpError(w, err)
				return
			}
			if u.Password != "" {
				pw := group.Password{Type: "plain"}
				pw.Key = &u.Password
				if err := group.SetUserPassword(g, u.Username, false, pw); err != nil {
					httpError(w, err)
					return
				}
			}
		}

		w.WriteHeader(http.StatusNoContent)
		return
	}

	http.NotFound(w, r)
}
