// Baci's read-only Native LiveKit capture. No browser, decoder, mixer, or encoder.
package main

import (
	"bufio"
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"net"
	"os"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/go-logr/logr"
	"github.com/livekit/protocol/livekit"
	"github.com/livekit/protocol/logger"
	lksdk "github.com/livekit/server-sdk-go/v2"
	"github.com/pion/rtcp"
	"github.com/pion/webrtc/v4"
	"golang.org/x/sys/unix"
)

const (
	sfu       = "ws://127.0.0.1:7880"
	limitMS   = 7_250_000
	maxTracks = 192
	maxIDs    = 4096 // includes mute/unmute and republish generations
)

var (
	identityPattern = regexp.MustCompile(`^(teacher|admin|student)-[1-9][0-9]{0,17}$`)
	roomPattern     = regexp.MustCompile(`^test-[a-f0-9]{20}-v[34]-c[1-9][0-9]{0,9}-[a-f0-9]{24}$`)
	sidPattern      = regexp.MustCompile(`^(PA|TR)_[A-Za-z0-9_-]{1,64}$`)
)

type command struct {
	Token   string `json:"token"`
	Room    string `json:"room"`
	Begin   int64  `json:"begin_ns"`
	Until   int64  `json:"until_ns"`
	Stop    bool   `json:"stop"`
	Mode    string `json:"media"`
	Teacher string `json:"teacher"`
	Select  bool   `json:"select_screen"`
	Screen  *owner `json:"screen_owner"`
}

func monotonic() (int64, error) {
	var now unix.Timespec
	err := unix.ClockGettime(unix.CLOCK_MONOTONIC, &now)
	return now.Nano(), err
}

func remaining(until int64) time.Duration {
	now, err := monotonic()
	if err != nil || until <= now {
		return 0
	}
	return min(8*time.Second, time.Duration(until-now))
}

type stream struct {
	id        atomic.Uint32 // zero while muted or unsubscribed
	pub       *lksdk.RemoteTrackPublication
	peer      *lksdk.RemoteParticipant
	track     *webrtc.TrackRemote
	source    string
	codec     string
	reportAt  uint32
	hasReport bool
}

type receiver struct {
	mu       sync.Mutex // serializes event order; no I/O under this lock
	origin   time.Time
	tracks   map[string]*stream
	out      pipe
	failed   chan struct{}
	fault    sync.Once
	exitCode int
	policy   policy
	seq      uint32
	ended    bool
	joined   bool
}

func (r *receiver) elapsed() uint32 {
	return uint32(max(0, min(limitMS, time.Since(r.origin).Milliseconds())))
}

func (r *receiver) fail() { r.failWith(nil) }

func (r *receiver) failWith(err error) {
	r.fault.Do(func() {
		r.exitCode = 2
		if errors.Is(err, errPressure) {
			r.exitCode = 3 // explicit backpressure, not a successful partial source
		}
		close(r.failed)
	})
}

func (r *receiver) outputOK() {
	if r.out.err != nil {
		r.failWith(r.out.err)
	}
}

func source(pub lksdk.TrackPublication) string {
	switch pub.Source() {
	case livekit.TrackSource_MICROPHONE:
		return "microphone"
	case livekit.TrackSource_CAMERA:
		return "camera"
	case livekit.TrackSource_SCREEN_SHARE:
		return "screen_share"
	}
	return ""
}

func eligible(pub lksdk.TrackPublication, p lksdk.Participant) bool {
	return identityPattern.MatchString(p.Identity()) && strings.HasPrefix(p.SID(), "PA_") && sidPattern.MatchString(p.SID()) &&
		strings.HasPrefix(pub.SID(), "TR_") && sidPattern.MatchString(pub.SID()) && source(pub) != ""
}

func (r *receiver) open(t *stream) {
	if r.seq >= maxIDs {
		r.fail()
		return
	}
	r.seq++
	t.hasReport = false
	r.out.event("open", r.seq, r.elapsed(), t.source, t.peer.Identity(), t.peer.SID(), t.pub.SID(), false)
	r.outputOK()
	t.id.Store(r.seq)
}

func (r *receiver) end(t *stream) {
	if id := t.id.Swap(0); id != 0 {
		r.out.event("end", id, r.elapsed())
		r.outputOK()
	}
}

func (r *receiver) attach(track *webrtc.TrackRemote, pub *lksdk.RemoteTrackPublication, p *lksdk.RemoteParticipant) {
	if !r.policy.allows(pub, p) {
		return
	}
	src, codec := source(pub), strings.ToLower(track.Codec().MimeType)
	if src == "microphone" && (codec != opus || track.Codec().ClockRate != 48000) ||
		src != "microphone" && ((codec != vp8 && codec != h264) || track.Codec().ClockRate != 90000) {
		r.fail() // Never present an unsupported capture as a complete film.
		return
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.ended {
		return
	}
	if old := r.tracks[pub.SID()]; old != nil {
		if old.track == track {
			return
		}
		r.end(old)
	} else if len(r.tracks) >= maxTracks {
		r.fail()
		return
	}
	t := &stream{pub: pub, peer: p, track: track, source: src, codec: codec}
	r.tracks[pub.SID()] = t
	pub.OnRTCP(func(packet rtcp.Packet) {
		report, ok := packet.(*rtcp.SenderReport)
		if ok && report.SSRC == uint32(track.SSRC()) && report.NTPTime != 0 {
			r.report(t, report)
		}
	})
	if !pub.IsMuted() {
		r.open(t)
	}
	go r.read(t)
}

func (r *receiver) detach(track *webrtc.TrackRemote, pub *lksdk.RemoteTrackPublication) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if t := r.tracks[pub.SID()]; !r.ended && t != nil && t.pub == pub && (track == nil || t.track == track) {
		r.end(t)
		delete(r.tracks, pub.SID())
	}
}

func (r *receiver) mute(pub lksdk.TrackPublication, peer lksdk.Participant) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if t := r.tracks[pub.SID()]; !r.ended && t != nil && t.pub == pub {
		if pub.IsMuted() || !r.policy.allows(pub, peer) {
			r.end(t)
		} else if t.id.Load() == 0 {
			r.open(t)
		}
	}
}

func (r *receiver) report(t *stream, sr *rtcp.SenderReport) {
	r.mu.Lock()
	defer r.mu.Unlock()
	id, at := t.id.Load(), r.elapsed()
	if r.ended || id == 0 || (t.hasReport && at-t.reportAt < 1000) {
		return
	}
	// Keep NTP only as a sender-clock observation, not server wall time. The
	// archive aligns each publisher's audio and video offline using these.
	ntpMS := (sr.NTPTime>>32)*1000 + (((sr.NTPTime & 0xffffffff) * 1000) >> 32)
	r.out.event("sr", id, at, sr.RTPTime, ntpMS)
	r.outputOK()
	t.reportAt, t.hasReport = at, true
}

func (r *receiver) frame(t *stream, id, ts uint32, key bool, frame []byte) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.ended || t.id.Load() != id {
		return // The generation ended while the frame was being assembled.
	}
	var storage [96]byte
	header := append(storage[:0], `["media",`...)
	header = strconv.AppendUint(header, uint64(id), 10)
	header = strconv.AppendUint(append(header, ','), uint64(r.elapsed()), 10)
	header = append(append(append(header, ',', '"'), t.codec...), '"', ',')
	header = strconv.AppendUint(header, uint64(ts), 10)
	header = strconv.AppendBool(append(header, ','), key)
	r.out.record(append(header, ']'), frame)
	r.outputOK()
}

func (r *receiver) read(t *stream) {
	defer r.detach(t.track, t.pub)
	var id uint32
	var assemble *assembler
	for {
		if t.track.SetReadDeadline(time.Now().Add(180*time.Millisecond)) != nil {
			return
		}
		for {
			packet, _, err := t.track.ReadRTP()
			if err != nil {
				var timeout net.Error
				if !errors.Is(err, os.ErrDeadlineExceeded) && !(errors.As(err, &timeout) && timeout.Timeout()) {
					return
				}
				if assemble != nil && id == t.id.Load() {
					assemble.drain(true)
				} else {
					assemble = nil
				}
				break
			}
			if current := t.id.Load(); id != current {
				id, assemble = current, nil
				if id != 0 {
					assemble = newAssembler(t.codec, func(ts uint32, key bool, frame []byte) {
						r.frame(t, current, ts, key, frame)
					}, func() { t.peer.WritePLI(t.track.SSRC()) })
				}
			}
			if assemble != nil {
				assemble.push(packet)
			}
		}
	}
}

func (r *receiver) flush() {
	r.mu.Lock()
	defer r.mu.Unlock()
	if !r.ended {
		r.out.flush()
		r.outputOK()
	}
}

func (r *receiver) finish() bool {
	r.mu.Lock()
	r.ended = true
	if !r.joined || r.elapsed() == 0 {
		r.mu.Unlock()
		return false
	}
	var length [4]byte
	binary.BigEndian.PutUint32(length[:], r.elapsed())
	r.out.flush()
	r.out.message(done, length[:])
	r.outputOK()
	close(r.out.out.messages)
	r.mu.Unlock()
	return <-r.out.out.finished == nil
}

func run(control io.Reader, output io.Writer) int {
	scanner := bufio.NewScanner(control)
	scanner.Buffer(make([]byte, 4096), 8192)
	var first command
	if !scanner.Scan() || json.Unmarshal(scanner.Bytes(), &first) != nil || first.Token == "" || len(first.Token) > 4096 ||
		!roomPattern.MatchString(first.Room) || remaining(first.Until) <= 0 || first.Stop || first.Select ||
		(first.Mode != "standard" && first.Mode != "audio" && first.Mode != "teacher-video") || !validOwner(first.Screen) ||
		(first.Teacher != "" && (!identityPattern.MatchString(first.Teacher) || !strings.HasPrefix(first.Teacher, "teacher-"))) {
		return 2
	}
	now, err := monotonic()
	if err != nil || first.Begin <= 0 || first.Begin > now || now-first.Begin > int64(10*time.Second) {
		return 2
	}
	r := &receiver{origin: time.Now().Add(-time.Duration(now - first.Begin)), tracks: make(map[string]*stream),
		failed: make(chan struct{}), policy: policy{mode: first.Mode, teacher: first.Teacher}}
	r.policy.screen.Store(first.Screen)
	r.out.out = newQueue(output, r.failWith)
	commands := make(chan command, 1)
	go func() {
		defer close(commands)
		for scanner.Scan() {
			var cmd command
			if json.Unmarshal(scanner.Bytes(), &cmd) != nil || cmd.Token != "" || cmd.Room != "" || cmd.Begin != 0 ||
				cmd.Mode != "" || cmd.Teacher != "" || (cmd.Screen != nil && !cmd.Select) || !validOwner(cmd.Screen) ||
				(cmd.Select && (cmd.Stop || cmd.Until != 0)) || (!cmd.Select && (cmd.Until > 0) == cmd.Stop) {
				r.fail()
				return
			}
			commands <- cmd
		}
	}()

	left := make(chan struct{}, 1)
	cb := lksdk.NewRoomCallback()
	changed := make(chan struct{}, 1)
	var reset atomic.Bool
	wake := func() {
		select {
		case changed <- struct{}{}:
		default:
		}
	}
	cb.OnTrackPublished = func(*lksdk.RemoteTrackPublication, *lksdk.RemoteParticipant) { wake() }
	cb.OnTrackSubscribed, cb.OnTrackMuted, cb.OnTrackUnmuted = r.attach, r.mute, r.mute
	cb.OnTrackUnsubscribed = func(track *webrtc.TrackRemote, pub *lksdk.RemoteTrackPublication, _ *lksdk.RemoteParticipant) {
		r.detach(track, pub)
	}
	cb.OnTrackUnpublished = func(pub *lksdk.RemoteTrackPublication, _ *lksdk.RemoteParticipant) { r.detach(nil, pub); wake() }
	cb.OnDisconnected = func() {
		select {
		case left <- struct{}{}:
		default:
		}
	}
	// The SDK copies callbacks at construction. Re-send desired subscriptions
	// after a full reconnect with new publications, without reusing old SIDs.
	cb.OnReconnected = func() { reset.Store(true); wake() }
	room := lksdk.NewRoom(cb)
	go func() {
		if err := room.JoinWithToken(sfu, first.Token, lksdk.WithAutoSubscribe(false), lksdk.WithDisableRegionDiscovery()); err != nil || room.Name() != first.Room {
			r.fail()
			return
		}
		r.mu.Lock()
		if r.ended {
			r.mu.Unlock()
			return
		}
		r.joined = true
		r.out.message(ready, nil)
		r.outputOK()
		r.mu.Unlock()
		wake()
		r.subscribe(room, changed, &reset)
	}()
	ticks := time.NewTicker(40 * time.Millisecond)
	defer ticks.Stop()
	go func() {
		for range ticks.C {
			r.flush()
		}
	}()
	hardEnd := first.Begin + int64(limitMS)*int64(time.Millisecond)
	until := min(first.Until, hardEnd)
	watchdog := time.NewTimer(remaining(until))
	defer watchdog.Stop()
loop:
	for {
		select {
		case <-r.failed:
			return r.exitCode
		case <-left:
			break loop
		case <-watchdog.C:
			break loop
		case cmd, ok := <-commands:
			if !ok {
				return 2 // The parent is gone; do not retain an orphan SFU subscription.
			}
			if cmd.Stop || remaining(until) <= 0 {
				break loop
			}
			if cmd.Select {
				r.policy.screen.Store(cmd.Screen)
				wake()
				continue
			}
			until = min(cmd.Until, hardEnd)
			watchdog.Reset(remaining(until))
		}
	}
	// Even if a pipe or potential network close blocks, the watchdog isn't
	// held by the writer mutex or SDK. This process owns the RTC sockets.
	closed := make(chan bool, 1)
	go func() {
		ok := r.finish()
		if ok {
			room.Disconnect()
		}
		closed <- ok
	}()
	select {
	case <-r.failed:
		return r.exitCode
	case ok := <-closed:
		if ok {
			select {
			case <-r.failed:
				return r.exitCode
			default:
				return 0
			}
		}
	case <-time.After(400 * time.Millisecond):
	}
	return 2
}

func main() {
	if len(os.Args) == 2 && os.Args[1] == "--check" {
		_, _ = io.WriteString(os.Stdout, protocol+"\n")
		return
	}
	if len(os.Args) != 1 {
		os.Exit(2)
	}
	lksdk.SetLogger(logger.LogRLogger(logr.Discard()))
	os.Exit(run(os.Stdin, os.Stdout))
}
