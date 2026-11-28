// Real WebRTC/SRTP load for the disposable nine-room VM benchmark. Build this
// alone using the native recorder's locked Go module, not on the measured VPS.
package main

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/go-logr/logr"
	"github.com/livekit/protocol/livekit"
	"github.com/livekit/protocol/logger"
	lksdk "github.com/livekit/server-sdk-go/v2"
	"github.com/pion/webrtc/v4"
	"github.com/pion/webrtc/v4/pkg/media"
	"github.com/pion/webrtc/v4/pkg/media/ivfreader"
)

const pupils = 20
const schools = 9
const slots = 3

var opus = webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeOpus, ClockRate: 48000, Channels: 2}
var vp8 = webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeVP8, ClockRate: 90000}

type ticket struct {
	Identity string `json:"identity"`
	Token    string `json:"token"`
}

type bell struct {
	Room    string   `json:"room"`
	Tickets []ticket `json:"tickets"`
}

type counts struct {
	bytes atomic.Uint64
	units atomic.Uint64
}

type stream struct {
	owner         int
	kind          livekit.TrackSource
	codec         webrtc.RTPCodecCapability
	width, height int
	fps           int
	frames        [][]byte
	sent          counts
}

type room struct {
	bell
	streams [slots]*stream
	peers   [pupils + 1]*lksdk.Room
	seen    [pupils + 1][slots]counts
}

func frames(path string, video bool) ([][]byte, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var out [][]byte
	if video {
		r, _, err := ivfreader.NewWith(f)
		if err != nil {
			return nil, err
		}
		for len(out) <= 160 {
			p, _, err := r.ParseNextFrame()
			if err == io.EOF && len(out) > 1 {
				return out, nil
			}
			if err != nil {
				return nil, err
			}
			out = append(out, p)
		}
	} else {
		for len(out) <= 150 {
			var n uint16
			err := binary.Read(f, binary.BigEndian, &n)
			if err == io.EOF && len(out) > 1 {
				return out, nil
			}
			if err != nil || n == 0 || n > 1275 {
				break
			}
			p := make([]byte, n)
			if _, err := io.ReadFull(f, p); err != nil {
				return nil, err
			}
			out = append(out, p)
		}
	}
	return nil, errors.New("invalid synthetic media file")
}

func (r *room) connect(url string, i int) error {
	cb := lksdk.NewRoomCallback()
	cb.OnTrackSubscribed = func(track *webrtc.TrackRemote, pub *lksdk.RemoteTrackPublication, from *lksdk.RemoteParticipant) {
		for k, s := range r.streams {
			if from.Identity() == r.Tickets[s.owner].Identity && pub.Source() == s.kind && strings.EqualFold(track.Codec().MimeType, s.codec.MimeType) {
				go func() {
					for {
						pkt, _, err := track.ReadRTP()
						if err != nil {
							return
						}
						r.seen[i][k].bytes.Add(uint64(len(pkt.Payload)))
					}
				}()
				return
			}
		}
	}
	var err error
	r.peers[i], err = lksdk.ConnectToRoomWithToken(url, r.Tickets[i].Token, cb, lksdk.WithDisableRegionDiscovery())
	if err != nil {
		// The SDK's signaling errors may include request URLs; don't log them.
		return fmt.Errorf("join failed in %s for peer %d (%T)", r.Room, i, err)
	}
	return nil
}

func send(ctx context.Context, s *stream, track *lksdk.LocalTrack, faults chan<- error) {
	period := time.Second / time.Duration(s.fps)
	tick := time.NewTicker(period)
	defer tick.Stop()
	for i := 0; ; i++ {
		select {
		case <-ctx.Done():
			return
		case <-tick.C:
		}
		p := s.frames[i%len(s.frames)]
		if err := track.WriteSample(media.Sample{Data: p, Duration: period}, nil); err != nil {
			if ctx.Err() == nil {
				faults <- errors.New("synthetic publisher stopped writing samples")
			}
			return
		}
		s.sent.bytes.Add(uint64(len(p)))
		s.sent.units.Add(1)
	}
}

func run(dir, profile string) error {
	lksdk.SetLogger(logger.LogRLogger(logr.Discard()))
	var pair struct {
		Site   string `json:"site"`
		URL    string `json:"signal_url"`
		Key    string `json:"api_key"`
		Secret string `json:"api_secret"`
	}
	read := func(name string, data any) error {
		file, err := os.ReadFile(filepath.Join(dir, name))
		if err != nil {
			return err
		}
		return json.Unmarshal(file, data)
	}
	if err := read("pair.json", &pair); err != nil || pair.URL != "wss://media.example.test" || pair.Site != "https://school.example.test/campus" {
		return errors.New("isolated QA pairing required")
	}
	var plans []bell
	if err := read("tickets.json", &plans); err != nil || len(plans) != schools {
		return errors.New("nine synthetic bells required")
	}
	var sounds [3][][]byte
	for i, file := range []string{"teacher.opus", "pupil.opus", "pupil2.opus"} {
		var err error
		if sounds[i], err = frames(filepath.Join(dir, file), false); err != nil {
			return err
		}
	}
	cam, err := frames(filepath.Join(dir, "camera.ivf"), true)
	if err != nil {
		return err
	}
	screen, err := frames(filepath.Join(dir, "screen.ivf"), true)
	if err != nil {
		return err
	}
	cw, cf, sw, sh, sf := 128, 8, 640, 360, 5
	if profile == "medium" {
		cw, cf, sw, sh, sf = 256, 12, 960, 540, 10
	} else if profile != "low" {
		return errors.New("unknown quality profile")
	}
	api := lksdk.NewRoomServiceClient(pair.URL, pair.Key, pair.Secret)
	ctx, cancel := context.WithTimeout(context.Background(), 6*time.Minute)
	defer cancel()
	rooms := make([]*room, schools)
	for k, p := range plans {
		want := fmt.Sprintf("test-%s-v4-c%d-%024x", strings.Repeat("a", 20), k+1, k+1)
		if p.Room != want || len(p.Tickets) != pupils+1 {
			return errors.New("unexpected room or seat count")
		}
		r := &room{bell: p}
		for i := range r.streams {
			r.streams[i] = &stream{owner: i, kind: livekit.TrackSource_MICROPHONE, codec: opus, fps: 50, frames: sounds[i]}
		}
		if k == 7 {
			r.streams[2] = &stream{owner: 0, kind: livekit.TrackSource_CAMERA, codec: vp8, width: cw, height: cw, fps: cf, frames: cam}
		} else if k == 8 {
			r.streams[2] = &stream{owner: 0, kind: livekit.TrackSource_SCREEN_SHARE, codec: vp8, width: sw, height: sh, fps: sf, frames: screen}
		}
		created, err := api.CreateRoom(ctx, &livekit.CreateRoomRequest{Name: p.Room, EmptyTimeout: 90, MaxParticipants: 24})
		if err != nil || created.GetMaxParticipants() != 24 {
			return fmt.Errorf("could not create synthetic room %d", k+1)
		}
		rooms[k] = r
		defer func() {
			if ctx.Err() == nil { // Also clean up if a benchmark assertion failed.
				done, stop := context.WithTimeout(context.Background(), 2*time.Second)
				defer stop()
				_, _ = api.DeleteRoom(done, &livekit.DeleteRoomRequest{Room: r.Room})
			}
		}()
	}
	joinStart := time.Now()
	var all sync.WaitGroup
	gate := make(chan struct{}, 16)
	joinErrors := make(chan error, schools*(pupils+1))
	for _, r := range rooms {
		for i := range r.peers {
			all.Go(func() {
				gate <- struct{}{}
				defer func() { <-gate }()
				if err := r.connect(pair.URL, i); err != nil {
					joinErrors <- err
				}
			})
		}
	}
	all.Wait()
	close(joinErrors)
	defer func() {
		var leaving sync.WaitGroup
		for _, r := range rooms {
			for _, peer := range r.peers {
				if peer != nil {
					leaving.Go(func() { peer.Disconnect() })
				}
			}
		}
		leaving.Wait()
	}()
	if err := <-joinErrors; err != nil {
		return err
	}
	fmt.Printf("QA_SCHOOL_JOIN pupils=180 teachers=9 rooms=9 seconds=%.2f stagger_limit=16\n", time.Since(joinStart).Seconds())
	if err := os.WriteFile(filepath.Join(dir, "peers-joined"), nil, 0600); err != nil {
		return err
	}
	faults := make(chan error, schools*slots)
	broadcast, stop := context.WithCancel(ctx)
	var writers sync.WaitGroup
	defer func() { stop(); writers.Wait() }()
	for _, r := range rooms {
		for i := 0; i < 3; i++ {
			var allowed []livekit.TrackSource
			for _, s := range r.streams {
				if s.owner == i {
					allowed = append(allowed, s.kind)
				}
			}
			if len(allowed) == 0 {
				continue
			}
			if _, err := api.UpdateParticipant(ctx, &livekit.UpdateParticipantRequest{Room: r.Room,
				Identity: r.Tickets[i].Identity, Permission: &livekit.ParticipantPermission{
					CanSubscribe: true, CanPublish: true, CanPublishSources: allowed,
				}}); err != nil {
				return errors.New("could not grant expected test source")
			}
		}
		for _, s := range r.streams {
			track, err := lksdk.NewLocalSampleTrack(s.codec)
			if err != nil {
				return err
			}
			if _, err := r.peers[s.owner].LocalParticipant.PublishTrack(track, &lksdk.TrackPublicationOptions{
				Source: s.kind, VideoWidth: s.width, VideoHeight: s.height,
			}); err != nil {
				return fmt.Errorf("publish %s in %s failed (%T)", s.kind, r.Room, err)
			}
			writers.Go(func() { send(broadcast, s, track, faults) })
		}
	}
	var sent, units [schools][slots]uint64
	var seen [schools][pupils + 1][slots]uint64
	var previous time.Time
	windows := 0
	tick := time.NewTicker(10 * time.Second)
	defer tick.Stop()
	for {
		select {
		case err := <-faults:
			return err
		case <-ctx.Done():
			return errors.New("load generation reached the test time limit")
		case now := <-tick.C:
			minBytes, minPace := 1., 1.
			var audio, camera, share, speakerPackets uint64
			for ri, r := range rooms {
				for k, s := range r.streams {
					total, u := s.sent.bytes.Load(), s.sent.units.Load()
					payload := total - sent[ri][k]
					newUnits := u - units[ri][k]
					sent[ri][k], units[ri][k] = total, u
					if !previous.IsZero() {
						if payload == 0 {
							return errors.New("expected track is not sending")
						}
						minPace = min(minPace, float64(newUnits)/(now.Sub(previous).Seconds()*float64(s.fps)))
					}
					switch s.kind {
					case livekit.TrackSource_MICROPHONE:
						audio += payload
						speakerPackets += newUnits
					case livekit.TrackSource_CAMERA:
						camera += payload
					case livekit.TrackSource_SCREEN_SHARE:
						share += payload
					}
					for pi := range r.peers {
						if pi == s.owner {
							continue
						}
						got := r.seen[pi][k].bytes.Load()
						if !previous.IsZero() {
							minBytes = min(minBytes, float64(got-seen[ri][pi][k])/float64(payload))
						}
						seen[ri][pi][k] = got
					}
				}
			}
			if !previous.IsZero() {
				windows++
				t := now.Sub(previous).Seconds()
				fmt.Printf("QA_SCHOOL_MEDIA window=%d min_payload_ratio=%.3f min_offer_pace=%.3f audio_kbps=%.1f camera_kbps=%.1f screen_kbps=%.1f audio_source_pps=%.0f\n",
					windows, minBytes, minPace, float64(audio)*.008/t, float64(camera)*.008/t, float64(share)*.008/t, float64(speakerPackets)/t)
				if minPace < .96 || minBytes < .90 {
					return errors.New("steady media was under-driven or did not reach every intended subscriber")
				}
				if windows == 1 {
					if err := os.WriteFile(filepath.Join(dir, "media-ready"), nil, 0600); err != nil {
						return err
					}
				}
			}
			previous = now
			if _, err := os.Stat(filepath.Join(dir, "media-stop")); err == nil {
				stop()
				writers.Wait()
				if err := os.WriteFile(filepath.Join(dir, "media-paused"), nil, 0600); err != nil {
					return err
				}
				// Peers stay present through the school's own retirement/consent.
				for i := 0; i < 80; i++ {
					if _, err := os.Stat(filepath.Join(dir, "rooms-closed")); err == nil {
						return nil
					}
					time.Sleep(250 * time.Millisecond)
				}
				return errors.New("school did not retire the nine synthetic bells")
			}
		}
	}
}

func main() {
	if len(os.Args) != 4 || os.Args[1] != "--fixture" {
		fmt.Fprintln(os.Stderr, "expected --fixture private-directory low|medium")
		os.Exit(1)
	}
	if err := run(os.Args[2], os.Args[3]); err != nil {
		fmt.Fprintln(os.Stderr, "QA_SCHOOL_LOAD failed:", err)
		os.Exit(1)
	}
}
