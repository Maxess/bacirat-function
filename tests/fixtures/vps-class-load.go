// Maintainer QA: real SRTP outside the resource-limited guest. Build with the
// receiver's pinned Go module. Only synthetic signals and fixed test names.
package main

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"os"
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

const roomName = "test-aaaaaaaaaaaaaaaaaaaa-v4-c15-bbbbbbbbbbbbbbbbbbbbbbbb"
const pupilCount = 10
const interval = 20 * time.Millisecond

type pairing struct {
	URL    string `json:"signal_url"`
	Key    string `json:"api_key"`
	Secret string `json:"api_secret"`
}

type channel struct {
	from        int
	source      livekit.TrackSource
	codec       webrtc.RTPCodecCapability
	width       int
	height      int
	every       int
	frames      [][]byte
	track       *lksdk.LocalTrack
	publication *lksdk.LocalTrackPublication
}

func movie(path string) ([][]byte, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	r, _, err := ivfreader.NewWith(f)
	if err != nil {
		return nil, err
	}
	var frames [][]byte
	for len(frames) <= 100 {
		f, _, err := r.ParseNextFrame()
		if err == io.EOF && len(frames) > 0 {
			return frames, nil
		}
		if err != nil {
			return nil, err
		}
		frames = append(frames, f)
	}
	return nil, fmt.Errorf("unexpected test IVF size")
}

func audio(path string) ([][]byte, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var packets [][]byte
	for len(packets) < 150 {
		var n uint16
		err := binary.Read(f, binary.BigEndian, &n)
		if err == io.EOF && len(packets) > 1 {
			return packets, nil
		}
		if err != nil || n == 0 || n > 1275 {
			return nil, fmt.Errorf("invalid synthetic Opus packet")
		}
		p := make([]byte, n)
		if _, err := io.ReadFull(f, p); err != nil {
			return nil, err
		}
		packets = append(packets, p)
	}
	return nil, fmt.Errorf("too many synthetic Opus packets")
}

func name(i int) string {
	if i == 0 {
		return "teacher-7"
	}
	return fmt.Sprintf("student-%d", i)
}

func connect(p pairing, i int, jwt string, streams []channel, counters *[pupilCount+1][4]atomic.Uint64) (*lksdk.Room, error) {
	cb := lksdk.NewRoomCallback()
	cb.OnTrackSubscribed = func(track *webrtc.TrackRemote, pub *lksdk.RemoteTrackPublication, from *lksdk.RemoteParticipant) {
		for index, s := range streams {
			if from.Identity() == name(s.from) && pub.Source() == s.source && strings.EqualFold(track.Codec().MimeType, s.codec.MimeType) {
				go func() {
					for {
						pkt, _, err := track.ReadRTP()
						if err != nil {
							return
						}
						counters[i][index].Add(uint64(len(pkt.Payload)))
					}
				}()
			}
		}
	}
	return lksdk.ConnectToRoomWithToken(p.URL, jwt, cb, lksdk.WithDisableRegionDiscovery())
}

func run() error {
	if len(os.Args) != 8 || os.Args[1] != "--fixture" {
		return fmt.Errorf("expected --fixture camera.ivf screen.ivf teacher.opus pupil.opus live-state.json tickets.json")
	}
	// The SDK validation-failure logger dumps entire HTTP requests. Only emit
	// our own fixed-stage diagnostics, never join tickets or request headers.
	lksdk.SetLogger(logger.LogRLogger(logr.Discard()))
	var pair pairing
	if err := json.NewDecoder(os.Stdin).Decode(&pair); err != nil {
		return err
	}
	if pair.URL != "wss://media.example.test" && pair.URL != "wss://classroom-new.example.com" {
		return fmt.Errorf("test fixture must use the isolated QA hostname")
	}
	ticketFile, err := os.Open(os.Args[7])
	if err != nil {
		return err
	}
	var tickets map[string]string
	err = json.NewDecoder(ticketFile).Decode(&tickets)
	_ = ticketFile.Close()
	if err != nil || len(tickets) != pupilCount+1 {
		return fmt.Errorf("expected one school join ticket per peer")
	}
	cam, err := movie(os.Args[2])
	if err != nil {
		return err
	}
	screen, err := movie(os.Args[3])
	if err != nil {
		return err
	}
	teacherMic, err := audio(os.Args[4])
	if err != nil {
		return err
	}
	pupilMic, err := audio(os.Args[5])
	if err != nil {
		return err
	}
	opus := webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeOpus, ClockRate: 48000, Channels: 2}
	vp8 := webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeVP8, ClockRate: 90000}
	streams := []channel{
		{from: 0, source: livekit.TrackSource_MICROPHONE, codec: opus, every: 1, frames: teacherMic},
		{from: 1, source: livekit.TrackSource_MICROPHONE, codec: opus, every: 1, frames: pupilMic},
		{from: 0, source: livekit.TrackSource_CAMERA, codec: vp8, width: 640, height: 360, every: 5, frames: cam},
		{from: 0, source: livekit.TrackSource_SCREEN_SHARE, codec: vp8, width: 1280, height: 720, every: 5, frames: screen},
	}
	ctx, stop := context.WithTimeout(context.Background(), 115*time.Second)
	defer stop()
	api := lksdk.NewRoomServiceClient(pair.URL, pair.Key, pair.Secret)
	room, err := api.CreateRoom(ctx, &livekit.CreateRoomRequest{Name: roomName, EmptyTimeout: 60, MaxParticipants: pupilCount + 1})
	if err != nil || room == nil || room.MaxParticipants != pupilCount+1 {
		return fmt.Errorf("configure real room cap for teacher plus %d pupils: %v", pupilCount, err)
	}
	defer func() {
		done, finish := context.WithTimeout(context.Background(), 4*time.Second)
		defer finish()
		_, _ = api.DeleteRoom(done, &livekit.DeleteRoomRequest{Room: roomName})
	}()
	var rooms [pupilCount + 1]*lksdk.Room
	var errors [pupilCount + 1]error
	var counters [pupilCount + 1][4]atomic.Uint64
	for start := 0; start <= pupilCount; start += 3 {
		var group sync.WaitGroup
		for i := start; i < start+3 && i <= pupilCount; i++ {
			group.Go(func() { rooms[i], errors[i] = connect(pair, i, tickets[name(i)], streams, &counters) })
		}
		group.Wait()
		for i := start; i < start+3 && i <= pupilCount; i++ {
			if rooms[i] != nil {
				defer rooms[i].Disconnect()
			}
			if errors[i] != nil {
				return fmt.Errorf("join %s: %w", name(i), errors[i])
			}
		}
	}
	for i := 0; i <= 1; i++ {
		allowed := []livekit.TrackSource{livekit.TrackSource_MICROPHONE}
		if i == 0 {
			allowed = append(allowed, livekit.TrackSource_CAMERA, livekit.TrackSource_SCREEN_SHARE)
		}
		if _, err := api.UpdateParticipant(ctx, &livekit.UpdateParticipantRequest{
			Room: roomName, Identity: name(i), Permission: &livekit.ParticipantPermission{
				CanSubscribe: true, CanPublish: true, CanPublishSources: allowed,
			},
		}); err != nil {
			return err
		}
	}
	for i := range streams {
		s := &streams[i]
		track, err := lksdk.NewLocalSampleTrack(s.codec)
		if err != nil {
			return err
		}
		s.track = track
		s.publication, err = rooms[s.from].LocalParticipant.PublishTrack(track, &lksdk.TrackPublicationOptions{
			Source: s.source, VideoWidth: s.width, VideoHeight: s.height,
		})
		if err != nil {
			return err
		}
	}
	tick := time.NewTicker(interval)
	defer tick.Stop()
	var previous, sent [4]uint64
	var old [pupilCount + 1][4]uint64
	for step := 0; step <= 3500; step++ {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-tick.C:
		}
		for i, s := range streams {
			if step%s.every == 0 {
				data := s.frames[(step/s.every)%len(s.frames)]
				if err := s.track.WriteSample(media.Sample{Data: data, Duration: time.Duration(s.every) * interval}, nil); err != nil {
					return err
				}
				sent[i] += uint64(len(data))
			}
		}
		if step%500 != 0 || step == 0 {
			continue
		}
		minimum := 1.0
		for i := range rooms {
			for j, s := range streams {
				if i == s.from {
					continue
				}
				now := counters[i][j].Load()
				delta := now - old[i][j]
				old[i][j] = now
				want := sent[j] - previous[j]
				ratio := float64(delta) / float64(want)
				minimum = min(minimum, ratio)
				if (step == 500 && ratio < .35) || (step > 500 && ratio < .85) {
					return fmt.Errorf("%s lacked %s from %s at %.0fs (payload-byte ratio %.2f)", name(i), s.source, name(s.from), float64(step)/50, ratio)
				}
			}
		}
		fmt.Printf("QA_LOAD window_end_s=%d pupils=10 teacher=1 min_payload_byte_ratio=%.3f cam_kbps=%.1f screen_kbps=%.1f\n",
			step/50, minimum, float64(sent[2]-previous[2])*8/10000, float64(sent[3]-previous[3])*8/10000)
		previous = sent
		if step == 1000 {
			status, err := json.Marshal(map[string]string{"teacherSID": rooms[0].LocalParticipant.SID(), "screenSID": streams[3].publication.SID()})
			if err != nil {
				return err
			}
			if err := os.WriteFile(os.Args[6]+".tmp", status, 0600); err != nil {
				return err
			}
			if err := os.Rename(os.Args[6]+".tmp", os.Args[6]); err != nil {
				return err
			}
		}
	}
	return nil
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "QA_LOAD failed:", err)
		os.Exit(1)
	}
}
