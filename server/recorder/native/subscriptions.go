package main

import (
	"slices"
	"strings"
	"sync/atomic"
	"time"

	lksdk "github.com/livekit/server-sdk-go/v2"
)

type owner struct {
	Identity string `json:"identity"`
	SID      string `json:"sid"`
}

func validOwner(o *owner) bool {
	return o == nil || (identityPattern.MatchString(o.Identity) && strings.HasPrefix(o.SID, "PA_") && sidPattern.MatchString(o.SID))
}

type policy struct {
	mode, teacher string
	screen        atomic.Pointer[owner]
}

func (p *policy) wants(source, identity, sid string) bool {
	switch source {
	case "microphone":
		return true
	case "camera":
		return p.mode == "standard" || (p.mode == "teacher-video" && identity == p.teacher)
	case "screen_share":
		o := p.screen.Load()
		return p.mode != "audio" && o != nil && o.Identity == identity && o.SID == sid
	}
	return false
}

func (p *policy) allows(pub lksdk.TrackPublication, peer lksdk.Participant) bool {
	return eligible(pub, peer) && p.wants(source(pub), peer.Identity(), peer.SID())
}

// All signaling writes are here, never under the receive/event lock. The
// recurring scan also sees permission changes for which the Go SDK has no
// callback; a lightweight wake handles publication and presenter changes.
func (r *receiver) subscribe(room *lksdk.Room, changed <-chan struct{}, reset *atomic.Bool) {
	tick := time.NewTicker(time.Second)
	defer tick.Stop()
	sent := make(map[*lksdk.RemoteTrackPublication]bool)
	for {
		select {
		case <-changed:
		case <-tick.C:
		case <-r.failed:
			return
		}
		if reset.Swap(false) {
			clear(sent)
		}
		present := make(map[*lksdk.RemoteTrackPublication]bool)
		for _, peer := range room.GetRemoteParticipants() {
			permission := peer.Permissions()
			for _, pub := range peer.TrackPublications() {
				remote, ok := pub.(*lksdk.RemoteTrackPublication)
				if !ok {
					continue
				}
				present[remote] = true
				allowed := permission.GetCanPublish() && (len(permission.GetCanPublishSources()) == 0 || slices.Contains(permission.GetCanPublishSources(), pub.Source()))
				want := allowed && r.policy.allows(pub, peer) && peer.GetTrackPublication(pub.Source()) == pub
				last, known := sent[remote]
				if !want {
					r.detach(nil, remote)
				}
				if (known && last == want) || (!known && !want && !remote.IsSubscribed()) {
					continue
				}
				if remote.SetSubscribed(want) == nil {
					sent[remote] = want
				}
			}
		}
		for pub := range sent {
			if !present[pub] {
				delete(sent, pub)
			}
		}
	}
}
