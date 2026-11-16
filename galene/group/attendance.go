package group

import (
	"bufio"
	"encoding/json"
	"log"
	"os"
	"path"
	"path/filepath"
	"sort"
	"sync"
	"time"
)

// AttendanceEvent records a single join or leave.  Events are stored
// one JSON object per line, which makes recording a cheap append and
// keeps the memory footprint negligible.
type AttendanceEvent struct {
	Username string    `json:"username"`
	Action   string    `json:"action"` // "join" or "leave"
	Time     time.Time `json:"time"`

	// group is unexported and therefore not serialised; it is only
	// used to route the event to the right file.
	group string
}

// AttendanceRecord summarises one user's attendance for a day.
type AttendanceRecord struct {
	Username     string      `json:"username"`
	Joins        []time.Time `json:"joins"`
	Leaves       []time.Time `json:"leaves"`
	TotalSeconds int64       `json:"totalSeconds"`
}

// attendanceCh decouples disk I/O from the group lock: joins and
// leaves are recorded asynchronously, so a slow disk never delays a
// client.  The channel is buffered; if it ever fills up (which means
// the disk is hopelessly slow) events are dropped and counted rather
// than blocking the whole server.
var attendanceCh = make(chan AttendanceEvent, 1024)
var attendanceOnce sync.Once

var attendanceMu sync.Mutex
var attendanceDropped int

func startAttendanceWriter() {
	attendanceOnce.Do(func() {
		go func() {
			for e := range attendanceCh {
				if err := writeAttendance(e); err != nil {
					log.Printf("attendance: %v", err)
				}
			}
		}()
	})
}

func attendanceDir() string {
	return filepath.Join(DataDirectory, "attendance")
}

// cleanGroupName sanitises a group name for use as a file name.  This
// prevents a crafted name from escaping the attendance directory.
func cleanGroupName(name string) string {
	return path.Clean("/" + name)[1:]
}

func attendanceFile(group, date string) string {
	return filepath.Join(attendanceDir(), cleanGroupName(group), date+".jsonl")
}

// RecordAttendance queues a join/leave event for writing.  It never
// blocks and never panics; failures are logged.
func RecordAttendance(group, username, action string) {
	if username == "" || group == "" {
		return
	}
	startAttendanceWriter()

	e := AttendanceEvent{
		Username: username,
		Action:   action,
		Time:     time.Now(),
		group:    group,
	}

	select {
	case attendanceCh <- e:
	default:
		attendanceMu.Lock()
		attendanceDropped++
		n := attendanceDropped
		attendanceMu.Unlock()
		if n == 1 || n%100 == 0 {
			log.Printf("attendance: dropping events (%v dropped)", n)
		}
	}
}

func writeAttendance(e AttendanceEvent) error {
	date := e.Time.Format("2006-01-02")
	file := attendanceFile(e.group, date)

	dir := filepath.Dir(file)
	if err := os.MkdirAll(dir, 0700); err != nil {
		return err
	}

	f, err := os.OpenFile(file, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	defer f.Close()

	enc := json.NewEncoder(f)
	return enc.Encode(e)
}

// GetAttendance returns the aggregated attendance records for a group
// on a given date (YYYY-MM-DD).  If date is empty, today's records are
// returned.
func GetAttendance(group, date string) ([]AttendanceRecord, error) {
	attendanceMu.Lock()
	defer attendanceMu.Unlock()

	if date == "" {
		date = time.Now().Format("2006-01-02")
	}
	file := attendanceFile(group, date)

	events, err := readAttendanceFile(file)
	if err != nil {
		return nil, err
	}

	byUser := make(map[string]*AttendanceRecord)
	joined := make(map[string]time.Time)
	for _, e := range events {
		rec, ok := byUser[e.Username]
		if !ok {
			rec = &AttendanceRecord{Username: e.Username}
			byUser[e.Username] = rec
		}
		switch e.Action {
		case "join":
			rec.Joins = append(rec.Joins, e.Time)
			joined[e.Username] = e.Time
		case "leave":
			rec.Leaves = append(rec.Leaves, e.Time)
			if t, ok := joined[e.Username]; ok {
				rec.TotalSeconds += int64(e.Time.Sub(t).Seconds())
				delete(joined, e.Username)
			}
		}
	}

	records := make([]AttendanceRecord, 0, len(byUser))
	for _, rec := range byUser {
		records = append(records, *rec)
	}
	sort.Slice(records, func(i, j int) bool {
		return records[i].Username < records[j].Username
	})
	return records, nil
}

func readAttendanceFile(file string) ([]AttendanceEvent, error) {
	f, err := os.Open(file)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	var events []AttendanceEvent
	scanner := bufio.NewScanner(f)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for scanner.Scan() {
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}
		var e AttendanceEvent
		if err := json.Unmarshal(line, &e); err != nil {
			continue
		}
		events = append(events, e)
	}
	return events, scanner.Err()
}

// GetAttendanceDates lists the dates for which attendance was recorded
// for a group, most recent first.
func GetAttendanceDates(group string) ([]string, error) {
	attendanceMu.Lock()
	defer attendanceMu.Unlock()

	dir := filepath.Join(attendanceDir(), cleanGroupName(group))
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}

	dates := make([]string, 0, len(entries))
	for _, e := range entries {
		name := e.Name()
		if len(name) != 15 || name[4] != '-' || name[7] != '-' {
			continue
		}
		dates = append(dates, name[:10])
	}
	sort.Sort(sort.Reverse(sort.StringSlice(dates)))
	return dates, nil
}
