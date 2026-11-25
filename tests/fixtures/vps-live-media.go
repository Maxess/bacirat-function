// Two test-only LiveKit clients in the VM's school-side network namespace.
// Build from server/recorder/native to reuse that pinned LiveKit/Pion SDK.
package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/livekit/protocol/auth"
	"github.com/livekit/protocol/livekit"
	lksdk "github.com/livekit/server-sdk-go/v2"
	"github.com/pion/webrtc/v4"
	"github.com/pion/webrtc/v4/pkg/media"
)

const roomName = "vps-live-media-fixture"

var testOpus = []byte{0xf8, 0xff, 0xfe} // 20 ms of Opus silence

func join(url, key, secret, identity, other string, seen chan<- string) (*lksdk.Room, error) {
	yes, no := true, false
	token, err := auth.NewAccessToken(key, secret).SetIdentity(identity).SetValidFor(time.Minute).
		SetVideoGrant(&auth.VideoGrant{RoomJoin: true, Room: roomName, CanSubscribe: &yes, CanPublish: &no, CanPublishData: &no}).ToJWT()
	if err != nil {
		return nil, err
	}
	cb := lksdk.NewRoomCallback()
	cb.OnTrackSubscribed = func(track *webrtc.TrackRemote, pub *lksdk.RemoteTrackPublication, sender *lksdk.RemoteParticipant) {
		if sender.Identity() != other {
			return
		}
		codec := track.Codec().MimeType
		if (codec != webrtc.MimeTypeOpus || pub.Source() != livekit.TrackSource_MICROPHONE) &&
			(codec != webrtc.MimeTypeVP8 || pub.Source() != livekit.TrackSource_CAMERA) {
			return
		}
		go func() {
			count := 0
			for {
				packet, _, err := track.ReadRTP()
				if err != nil {
					return
				}
				if (codec == webrtc.MimeTypeOpus && bytes.Equal(packet.Payload, testOpus)) ||
					(codec == webrtc.MimeTypeVP8 && len(packet.Payload) > 20) {
					count++
					if count == 30 {
						select {
						case seen <- identity + ":" + codec:
						default:
						}
					}
				}
			}
		}()
	}
	return lksdk.ConnectToRoomWithToken(url, token, cb, lksdk.WithDisableRegionDiscovery())
}

func publish(room *lksdk.Room, codec webrtc.RTPCodecCapability, source livekit.TrackSource) (*lksdk.LocalTrack, error) {
	track, err := lksdk.NewLocalSampleTrack(codec)
	if err != nil {
		return nil, err
	}
	_, err = room.LocalParticipant.PublishTrack(track, &lksdk.TrackPublicationOptions{
		Source: source, VideoWidth: 32, VideoHeight: 32,
	})
	return track, err
}

func exercise() error {
	var pair struct {
		URL    string `json:"signal_url"`
		Key    string `json:"api_key"`
		Secret string `json:"api_secret"`
	}
	if err := json.NewDecoder(os.Stdin).Decode(&pair); err != nil {
		return err
	}
	if pair.URL != "wss://media.example.test" && pair.URL != "wss://classroom-new.example.com" {
		return fmt.Errorf("test client only runs against the isolated rehearsal names")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 35*time.Second)
	defer cancel()
	api := lksdk.NewRoomServiceClient(pair.URL, pair.Key, pair.Secret)
	if _, err := api.CreateRoom(ctx, &livekit.CreateRoomRequest{Name: roomName, EmptyTimeout: 60, MaxParticipants: 2}); err != nil {
		return fmt.Errorf("signed CreateRoom: %w", err)
	}
	defer func() {
		done, stop := context.WithTimeout(context.Background(), 4*time.Second)
		defer stop()
		_, _ = api.DeleteRoom(done, &livekit.DeleteRoomRequest{Room: roomName})
	}()

	seen := make(chan string, 8)
	names := [2]string{"teacher-11", "student-12"}
	var speakers [2]*lksdk.LocalTrack
	var camera *lksdk.LocalTrack
	for i, name := range names {
		room, err := join(pair.URL, pair.Key, pair.Secret, name, names[1-i], seen)
		if err != nil {
			return fmt.Errorf("signaling/admission for %s: %w", name, err)
		}
		defer room.Disconnect()
		sources := []livekit.TrackSource{livekit.TrackSource_MICROPHONE}
		if i == 0 {
			sources = append(sources, livekit.TrackSource_CAMERA)
		}
		_, err = api.UpdateParticipant(ctx, &livekit.UpdateParticipantRequest{
			Room: roomName, Identity: name, Permission: &livekit.ParticipantPermission{
				CanSubscribe: true, CanPublish: true, CanPublishSources: sources,
			},
		})
		if err != nil {
			return fmt.Errorf("grant media sources for %s: %w", name, err)
		}
		speakers[i], err = publish(room, webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeOpus, ClockRate: 48000, Channels: 2}, livekit.TrackSource_MICROPHONE)
		if err != nil {
			return fmt.Errorf("publish microphone for %s: %w", name, err)
		}
		if i == 0 {
			camera, err = publish(room, webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeVP8, ClockRate: 90000}, livekit.TrackSource_CAMERA)
			if err != nil {
				return fmt.Errorf("publish camera: %w", err)
			}
		}
	}

	// One synthetic 32x32 black VP8 keyframe from libvpx, no student media.
	frame, err := base64.StdEncoding.DecodeString("MAIAnQEqIAAgAABHCIWFiIWEiAICAAeQ88nA/v+rUIA=")
	if err != nil {
		return err
	}
	const interval = 20 * time.Millisecond
	tick := time.NewTicker(interval)
	defer tick.Stop()
	want := map[string]bool{
		names[0] + ":" + webrtc.MimeTypeOpus: true,
		names[1] + ":" + webrtc.MimeTypeOpus: true,
		names[1] + ":" + webrtc.MimeTypeVP8:  true,
	}
	for n := 0; len(want) > 0; {
		select {
		case <-ctx.Done():
			return fmt.Errorf("did not receive all media: %v", want)
		case received := <-seen:
			delete(want, received)
		case <-tick.C:
			for _, speaker := range speakers {
				if err := speaker.WriteSample(media.Sample{Data: testOpus, Duration: interval}, nil); err != nil {
					return err
				}
			}
			if n%3 == 0 {
				if err := camera.WriteSample(media.Sample{Data: frame, Duration: 3 * interval}, nil); err != nil {
					return err
				}
			}
			n++
		}
	}
	return nil
}

func main() {
	if err := exercise(); err != nil {
		fmt.Fprintln(os.Stderr, "VM WebRTC fixture failed:", err)
		os.Exit(1)
	}
	fmt.Println("Two live WebRTC peers exchanged bidirectional Opus and teacher VP8 through the installed SFU.")
}
