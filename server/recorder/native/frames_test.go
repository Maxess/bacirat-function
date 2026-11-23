package main

import (
	"bytes"
	"encoding/binary"
	"errors"
	"io"
	"testing"
	"time"

	"github.com/pion/rtp"
)

func packet(seq uint16, ts uint32, mark bool, payload ...byte) *rtp.Packet {
	return &rtp.Packet{Header: rtp.Header{Version: 2, SequenceNumber: seq, Timestamp: ts, Marker: mark}, Payload: payload}
}

func TestVP8WrapReorderAndLoss(t *testing.T) {
	key := []byte{0x10, 0x10, 0, 0, 0x9d, 1, 0x2a, 1, 0, 1, 0}
	delta := []byte{0x10, 1, 1, 1}
	var got []uint32
	var nacks int
	a := newAssembler(vp8, func(ts uint32, _ bool, _ []byte) { got = append(got, ts) }, func() { nacks++ })
	a.push(packet(65533, 0xffffff00, true, key...))
	a.push(packet(65535, 0xffffff00+0x80, true, 0x11, 0x99)) // start of a secondary partition, not a new frame
	a.push(packet(65534, 0xffffff00+0x80, false, delta...))
	a.push(packet(1, 100, true, delta...)) // sequence 0 is lost: don't decode a dangling delta
	a.drain(true)
	a.push(packet(2, 200, true, key...))
	a.push(packet(2, 200, true, key...))
	want := []uint32{0xffffff00, 0xffffff80, 200}
	if len(got) != len(want) || got[0] != want[0] || got[1] != want[1] || got[2] != want[2] || nacks != 1 {
		t.Fatalf("bad frame recovery: %x, key requests: %d", got, nacks)
	}
}

func TestH264FragmentationAndParameterSets(t *testing.T) {
	var got [][]byte
	a := newAssembler(h264, func(_ uint32, key bool, b []byte) {
		if !key {
			t.Fatal("unexpected delta")
		}
		got = append(got, bytes.Clone(b))
	}, func() {})
	a.push(packet(1, 500, false, 0x67, 42, 0x80))
	a.push(packet(2, 500, false, 0x68, 0xce, 0x81))
	a.push(packet(3, 500, false, 0x7c, 0x85, 1, 2))
	a.push(packet(4, 500, true, 0x7c, 0x45, 3, 4))
	a.push(packet(5, 1000, true, 0x65, 4, 5)) // cached sets must prefix the IDR
	want := []byte{0, 0, 0, 1, 0x67, 42, 0x80, 0, 0, 0, 1, 0x68, 0xce, 0x81, 0, 0, 0, 1, 0x65}
	if len(got) != 2 || !bytes.Equal(got[0], append(bytes.Clone(want), 1, 2, 3, 4)) ||
		!bytes.Equal(got[1], append(bytes.Clone(want), 4, 5)) {
		t.Fatalf("wrong Annex B access units: %x", got)
	}
}

func TestOpusIsCopiedAsIs(t *testing.T) {
	var got [][]byte
	a := newAssembler(opus, func(_ uint32, key bool, b []byte) {
		if !key {
			t.Fatal("audio packet is a self-contained access unit")
		}
		got = append(got, bytes.Clone(b))
	}, func() { t.Fatal("key request on audio") })
	a.push(packet(8, 5000, false, 0x78, 1, 2))
	a.push(packet(8, 5000, false, 0x78, 1, 2))
	a.push(packet(10, 6920, false, 0x78, 9))
	a.drain(true)
	if len(got) != 2 || !bytes.Equal(got[0], []byte{0x78, 1, 2}) || !bytes.Equal(got[1], []byte{0x78, 9}) {
		t.Fatalf("wrong Opus payloads: %x", got)
	}
}

func TestWireKeepsRawPayload(t *testing.T) {
	var output bytes.Buffer
	q := newQueue(&output, func(error) {})
	p := pipe{out: q}
	header := []byte(`["media",1,2,"audio/opus",3,true]`)
	payload := []byte{0xf8, 0xff, 0xfe}
	p.record(header, payload)
	p.flush()
	close(q.messages)
	if err := <-q.finished; err != nil {
		t.Fatal(err)
	}
	b := output.Bytes()
	if b[0] != data || binary.BigEndian.Uint32(b[1:5]) != uint32(12+len(header)+len(payload)) ||
		!bytes.Equal(b[17:17+len(header)], header) || !bytes.Equal(b[len(b)-len(payload):], payload) {
		t.Fatalf("bad record: %x", b)
	}
	p.record(header, make([]byte, maxFrame+1))
	if p.err == nil {
		t.Fatal("unbounded frame")
	}
}

func TestReceiverQueueDoesNotWaitForBlockedPipe(t *testing.T) {
	read, write := io.Pipe() // intentionally never read from it while queuing
	defer read.Close()
	defer write.Close()
	q := newQueue(write, func(error) {})
	done := make(chan error, 1)
	go func() {
		for i := 0; i < queueBlocks+1; i++ {
			if err := q.put(data, make([]byte, maxFrame)); err != nil {
				done <- err
				return
			}
		}
		done <- nil
	}()
	select {
	case err := <-done:
		if !errors.Is(err, errPressure) || q.pending.Load() != queueBytes {
			t.Fatalf("did not cap bytes: %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("media path blocked by pipe writer")
	}
}

func TestRoomProfilesKeepAudioAndOnlyNeededVideo(t *testing.T) {
	for _, mode := range []string{"standard", "teacher-video", "audio"} {
		p := policy{mode: mode, teacher: "teacher-7"}
		p.screen.Store(&owner{Identity: "student-1", SID: "PA_old"})
		checks := []struct {
			source, identity, sid string
			want                  bool
		}{
			{"microphone", "teacher-7", "PA_t", true},
			{"microphone", "student-1", "PA_old", true},
			{"camera", "teacher-7", "PA_t", mode != "audio"},
			{"camera", "teacher-8", "PA_other", mode == "standard"},
			{"camera", "student-1", "PA_old", mode == "standard"},
			{"screen_share", "student-1", "PA_old", mode != "audio"},
			{"screen_share", "student-1", "PA_new", false},
			{"screen_share", "teacher-7", "PA_t", false},
			{"screen_share_audio", "student-1", "PA_old", false},
		}
		for _, check := range checks {
			if p.wants(check.source, check.identity, check.sid) != check.want {
				t.Fatalf("wrong selection for %s: %+v", mode, check)
			}
		}
	}
}
