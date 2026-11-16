// Package school synchronises the class server with the school's
// management database.
//
// The school management system remains untouched: it only has to
// produce a JSON export, which this package turns into Galene groups.
// Authentication then happens entirely locally, so a class can run
// even when the management database is unreachable.
package school

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"golang.org/x/crypto/bcrypt"

	"github.com/jech/galene/group"
)

// Export is the contract between the school management system and the
// class server.  Anything that can write this shape — a MySQL query, a
// PHP script, a cron job — can feed the class server.
type Export struct {
	// AcademicYear is informational: only classes of the current year
	// should be exported, but the sync does not enforce it.
	AcademicYear string        `json:"academicYear"`
	Classes      []ClassExport `json:"classes"`
}

// ClassExport is one school class and its members.
type ClassExport struct {
	// Id is the class id in the management database.  It is used to
	// build the group name, and is the only stable identifier: the
	// class name may be edited at any time.
	Id       int             `json:"id"`
	Name     string          `json:"name"`
	Grade    string          `json:"grade"`
	Students []StudentExport `json:"students"`
	Teachers []TeacherExport `json:"teachers"`
}

// StudentExport is one enrolled student.
type StudentExport struct {
	NationalId string `json:"nationalId"`
	FirstName  string `json:"firstName"`
	LastName   string `json:"lastName"`
	// Serial is the six-digit birth certificate serial, which is the
	// student's password in the management system.
	Serial string `json:"serial"`
}

// TeacherExport is one teacher assigned to the class.
type TeacherExport struct {
	NationalId    string `json:"nationalId"`
	FirstName     string `json:"firstName"`
	LastName      string `json:"lastName"`
	PersonnelCode string `json:"personnelCode"`
}

// Result reports what a synchronisation did.
type Result struct {
	Classes  int      `json:"classes"`
	Users    int      `json:"users"`
	Removed  int      `json:"removed"`
	Errors   []string `json:"errors,omitempty"`
	Duration string   `json:"duration"`
}

// Config controls where the export comes from.
type Config struct {
	// ExportFile is a path to a JSON file.  Relative paths are
	// resolved against the data directory.
	ExportFile string `json:"exportFile"`

	// ExportCommand, if set, is run before every sync and is expected
	// to write the export to ExportFile (or to stdout, see
	// CommandWritesStdout).  This is what makes periodic syncing
	// possible without any extra service.
	ExportCommand       string `json:"exportCommand"`
	CommandWritesStdout bool   `json:"commandWritesStdout"`

	// SyncInterval, e.g. "1h".  Empty disables periodic syncing; the
	// sync can then still be triggered from the admin panel.
	SyncInterval string `json:"syncInterval"`

	// BcryptCost is the cost used when hashing passwords.  The
	// default of 8 matches the rest of Galene and keeps the load on
	// weak servers low.
	BcryptCost int `json:"bcryptCost"`
}

var (
	mu     sync.Mutex
	config Config
	loaded bool

	// names maps "group\0username" to the display name, so that the
	// class list can show real names instead of national ids.
	names       map[string]string
	lastRun     time.Time
	lastErr     error
	lastResult  Result
)

// DefaultExportFile is where the export is read from when nothing is
// configured.
const DefaultExportFile = "school-export.json"

// DefaultBcryptCost matches the cost Galene itself uses.
const DefaultBcryptCost = 8

func dataDir() string {
	return group.DataDirectory
}

// ConfigFile is where the school integration is configured, relative
// to the data directory.
const ConfigFile = "school.json"

// LoadConfig reads the configuration from the data directory.  A
// missing file is not an error: the integration simply stays idle.
func LoadConfig() (Config, error) {
	data, err := os.ReadFile(exportPath(ConfigFile))
	if err != nil {
		return Config{}, err
	}
	var c Config
	if err := json.Unmarshal(data, &c); err != nil {
		return Config{}, fmt.Errorf("parse %v: %w", ConfigFile, err)
	}
	return c, nil
}

// SetConfig installs the configuration.  It is called once at startup.
func SetConfig(c Config) {
	mu.Lock()
	defer mu.Unlock()
	config = c
	if config.ExportFile == "" {
		config.ExportFile = DefaultExportFile
	}
	if config.BcryptCost <= 0 {
		config.BcryptCost = DefaultBcryptCost
	}
}

// GetConfig returns the active configuration.
func GetConfig() Config {
	mu.Lock()
	defer mu.Unlock()
	if config.ExportFile == "" {
		config.ExportFile = DefaultExportFile
	}
	if config.BcryptCost <= 0 {
		config.BcryptCost = DefaultBcryptCost
	}
	return config
}

func exportPath(file string) string {
	if filepath.IsAbs(file) {
		return file
	}
	return filepath.Join(dataDir(), file)
}

// Load reads the export, running the configured command first if any.
func Load() (*Export, error) {
	c := GetConfig()

	var data []byte
	if c.ExportCommand != "" {
		out, err := runCommand(c.ExportCommand)
		if err != nil {
			return nil, err
		}
		if c.CommandWritesStdout {
			data = out
		}
	}
	if c.CommandWritesStdout || c.ExportCommand == "" || data == nil {
		b, err := os.ReadFile(exportPath(c.ExportFile))
		if err != nil {
			return nil, err
		}
		data = b
	}

	var e Export
	if err := json.Unmarshal(data, &e); err != nil {
		return nil, fmt.Errorf("parse school export: %w", err)
	}
	return &e, nil
}

// runCommand runs the export command with a hard timeout, so that a
// hanging script can never wedge the class server.
func runCommand(command string) ([]byte, error) {
	if strings.TrimSpace(command) == "" {
		return nil, errors.New("empty export command")
	}
	ctx, cancel := context.WithTimeout(
		context.Background(), 2*time.Minute,
	)
	defer cancel()

	cmd := exec.CommandContext(ctx, "/bin/sh", "-c", command)
	out, err := cmd.Output()
	if ctx.Err() == context.DeadlineExceeded {
		return nil, errors.New("export command timed out")
	}
	if err != nil {
		return nil, fmt.Errorf("export command: %w", err)
	}
	return out, nil
}

// Status reports the last synchronisation.
type Status struct {
	Configured bool   `json:"configured"`
	LastRun    string `json:"lastRun,omitempty"`
	LastError  string `json:"lastError,omitempty"`
	Classes    int    `json:"classes"`
	Users      int    `json:"users"`
}

// GetStatus returns the state of the last synchronisation.
func GetStatus() Status {
	mu.Lock()
	defer mu.Unlock()

	s := Status{
		Configured: loaded,
		Classes:    lastResult.Classes,
		Users:      lastResult.Users,
	}
	if !lastRun.IsZero() {
		s.LastRun = lastRun.Format(time.RFC3339)
	}
	if lastErr != nil {
		s.LastError = lastErr.Error()
	}
	return s
}

// DisplayName returns the name to show for a username in a group.
func DisplayName(group, username string) (string, bool) {
	mu.Lock()
	defer mu.Unlock()
	if names == nil {
		return "", false
	}
	n, ok := names[group+"\x00"+username]
	return n, ok
}

// fullName joins the parts of a name, dropping empty ones.
func fullName(parts ...string) string {
	kept := make([]string, 0, len(parts))
	for _, p := range parts {
		if p = strings.TrimSpace(p); p != "" {
			kept = append(kept, p)
		}
	}
	return strings.Join(kept, " ")
}

// Sync turns the export into groups.  It is safe to call repeatedly:
// existing groups and users are updated in place, and members who are
// no longer enrolled are removed.
func Sync() (*Result, error) {
	e, err := Load()
	if err != nil {
		mu.Lock()
		lastErr = err
		mu.Unlock()
		return nil, err
	}
	return syncExport(e)
}

func syncExport(e *Export) (*Result, error) {
	start := time.Now()
	res := &Result{}
	newNames := make(map[string]string)

	for _, c := range e.Classes {
		if c.Id <= 0 {
			res.Errors = append(res.Errors,
				fmt.Sprintf("class %q has no id, skipped", c.Name))
			continue
		}
		name := GroupName(c.Id)

		if err := syncGroup(name, c, newNames, res); err != nil {
			res.Errors = append(res.Errors,
				fmt.Sprintf("class %v (%v): %v", c.Id, c.Name, err))
			continue
		}
		res.Classes++
	}

	res.Duration = time.Since(start).Round(time.Millisecond).String()

	mu.Lock()
	names = newNames
	loaded = true
	lastRun = time.Now()
	lastErr = nil
	lastResult = *res
	mu.Unlock()

	return res, nil
}

// GroupName returns the Galene group name for a class id.  Using the
// numeric id keeps the address stable when a class is renamed, and
// keeps it ASCII so that URLs and tokens never need escaping.
func GroupName(id int) string {
	return fmt.Sprintf("class-%v", id)
}

func syncGroup(name string, c ClassExport, newNames map[string]string, res *Result) error {
	// The full description keeps the stored password hashes, which is
	// how we avoid re-hashing every member on every run.
	desc, err := group.GetDescription(name)
	exists := true
	if errors.Is(err, os.ErrNotExist) {
		exists = false
		err = nil
	}
	if err != nil {
		return err
	}

	etag := ""
	if exists {
		etag, err = group.GetDescriptionTag(name)
		if err != nil {
			return err
		}
	}

	// Update the group itself.  UpdateDescription rejects a
	// description carrying users, so the copy is stripped first;
	// fields the export does not describe (recording, subgroups, ...)
	// are preserved rather than reset on every run.
	var newdesc *group.Description
	if exists {
		merged := *desc
		merged.DisplayName = c.Name
		merged.Description = c.Grade
		merged.Users = nil
		merged.WildcardUser = nil
		merged.AuthKeys = nil
		newdesc = &merged
	} else {
		newdesc = &group.Description{
			DisplayName: c.Name,
			Description: c.Grade,
			Public:      false,
		}
	}
	if err := group.UpdateDescription(name, etag, newdesc); err != nil {
		return err
	}

	// Work out the members we want.
	type member struct {
		username string
		role     string
		password string
		name     string
	}
	members := make(map[string]member, len(c.Students)+len(c.Teachers))

	for _, s := range c.Students {
		username := strings.TrimSpace(s.NationalId)
		if username == "" {
			continue
		}
		members[username] = member{
			username: username,
			role:     "student",
			// The password is the six-digit birth certificate
			// serial, exactly as in the management system.
			password: firstNonEmpty(s.Serial, s.NationalId),
			name:     fullName(s.FirstName, s.LastName),
		}
	}
	for _, t := range c.Teachers {
		username := strings.TrimSpace(t.NationalId)
		if username == "" {
			continue
		}
		// A teacher who also appears as a student keeps the
		// higher role.
		if m, ok := members[username]; ok && m.role == "teacher" {
			continue
		}
		members[username] = member{
			username: username,
			role:     "teacher",
			password: firstNonEmpty(t.PersonnelCode, t.NationalId),
			name:     fullName(t.FirstName, t.LastName),
		}
	}

	// Remove members who are no longer enrolled, except operators,
	// which are managed by hand.
	if desc != nil {
		for username, u := range desc.Users {
			if _, ok := members[username]; ok {
				continue
			}
			if containsPermission(u.Permissions, "op") {
				continue
			}
			uetag, err := group.GetUserTag(name, username, false)
			if err != nil {
				res.Errors = append(res.Errors,
					fmt.Sprintf("remove %v: %v", username, err))
				continue
			}
			if err := group.DeleteUser(name, username, false, uetag); err != nil {
				res.Errors = append(res.Errors,
					fmt.Sprintf("remove %v: %v", username, err))
				continue
			}
			res.Removed++
		}
	}

	// Add or update members.  Sorted so that runs are reproducible.
	usernames := make([]string, 0, len(members))
	for u := range members {
		usernames = append(usernames, u)
	}
	sort.Strings(usernames)

	cost := GetConfig().BcryptCost

	for _, username := range usernames {
		m := members[username]

		perms, err := group.NewPermissions(m.role)
		if err != nil {
			res.Errors = append(res.Errors,
				fmt.Sprintf("%v: %v", username, err))
			continue
		}

		uetag, err := group.GetUserTag(name, username, false)
		if errors.Is(err, os.ErrNotExist) {
			uetag = ""
			err = nil
		}
		if err != nil {
			res.Errors = append(res.Errors,
				fmt.Sprintf("%v: %v", username, err))
			continue
		}

		if err := group.UpdateUser(name, username, false, uetag,
			&group.UserDescription{Permissions: perms}); err != nil {
			res.Errors = append(res.Errors,
				fmt.Sprintf("%v: %v", username, err))
			continue
		}

		// Rehash only when the stored password no longer matches the
		// one the management system expects.  Hashing is the
		// expensive part of a sync, and it would otherwise run for
		// every member on every run.
		if exists && passwordMatches(desc, username, m.password) {
			res.Users++
			if m.name != "" {
				newNames[name+"\x00"+username] = m.name
			}
			continue
		}
		if err := setPassword(name, username, m.password, cost); err != nil {
			res.Errors = append(res.Errors,
				fmt.Sprintf("%v: %v", username, err))
			continue
		}
		res.Users++

		if m.name != "" {
			newNames[name+"\x00"+username] = m.name
		}
	}

	return nil
}

// passwordMatches reports whether the stored password for a user still
// matches the one the management system expects.  It returns false for
// a user who does not exist yet.
func passwordMatches(desc *group.Description, username, password string) bool {
	if desc == nil || desc.Users == nil {
		return false
	}
	u, ok := desc.Users[username]
	if !ok {
		return false
	}
	ok, err := u.Password.Match(password)
	if err != nil {
		// A corrupt hash is treated as a mismatch, so that the next
		// step replaces it with a good one.
		return false
	}
	return ok
}

// setPassword hashes a password with the configured cost and stores it.
func setPassword(groupname, username, password string, cost int) error {
	key, err := bcrypt.GenerateFromPassword([]byte(password), cost)
	if err != nil {
		return err
	}
	k := string(key)
	return group.SetUserPassword(groupname, username, false,
		group.Password{Type: "bcrypt", Key: &k},
	)
}

func firstNonEmpty(values ...string) string {
	for _, v := range values {
		if strings.TrimSpace(v) != "" {
			return strings.TrimSpace(v)
		}
	}
	return ""
}

func containsPermission(p group.Permissions, want string) bool {
	for _, perm := range p.Permissions(nil) {
		if perm == want {
			return true
		}
	}
	return false
}

// Start runs the synchronisation in the background.  Without a
// configuration it does nothing at all, so that an installation that
// does not use a school database is unaffected.
func Start() {
	c := GetConfig()

	// Without an export file and without a command to produce one,
	// there is nothing to import.
	if _, err := os.Stat(exportPath(c.ExportFile)); err != nil &&
		c.ExportCommand == "" {
		return
	}

	var interval time.Duration
	if c.SyncInterval != "" {
		d, err := time.ParseDuration(c.SyncInterval)
		if err != nil {
			log.Printf("School sync: bad syncInterval %q: %v",
				c.SyncInterval, err)
		} else {
			interval = d
		}
	}

	go func() {
		// Sync once at startup, so that a fresh installation is
		// usable straight away even without periodic syncing.
		logSync(Sync())
		if interval <= 0 {
			return
		}
		for {
			time.Sleep(interval)
			logSync(Sync())
		}
	}()
}

func logSync(res *Result, err error) {
	if err != nil {
		log.Printf("School sync: %v", err)
		return
	}
	log.Printf("School sync: %v classes, %v users, %v removed (%v)",
		res.Classes, res.Users, res.Removed, res.Duration)
	for _, e := range res.Errors {
		log.Printf("School sync: %v", e)
	}
}
