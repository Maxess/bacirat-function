package main

import (
	"bytes"
	"time"

	"github.com/livekit/server-sdk-go/v2/pkg/samplebuilder"
	"github.com/pion/rtp"
	"github.com/pion/rtp/codecs"
)

// A start of VP8 partition 1+ is not the start of a new picture.
type vp8Frame struct{ codecs.VP8Packet }

func (*vp8Frame) IsPartitionHead(payload []byte) bool {
	return len(payload) > 0 && payload[0]&0x17 == 0x10
}

const (
	opus = "audio/opus"
	vp8  = "video/vp8"
	h264 = "video/h264"
)

// No codec is decoded here. The bounded RTP reorder buffer only rebuilds
// access units and discards incomplete ones, resuming video on a key frame.
type assembler struct {
	codec     string
	builder   *samplebuilder.SampleBuilder
	depacket  rtp.Depacketizer
	seen      bool
	seq       uint16
	lastTS    uint32
	hasTS     bool
	needKey   bool
	lastPLI   time.Time
	sps, pps  []byte
	pending   []byte // H.264's multiple NALs can have one RTP timestamp.
	pendingTS uint32
	onFrame   func(ts uint32, key bool, b []byte)
	request   func()
}

func newAssembler(codec string, onFrame func(uint32, bool, []byte), request func()) *assembler {
	a := &assembler{codec: codec, onFrame: onFrame, request: request, needKey: codec != opus}
	var rate uint32 = 90000
	var window uint16 = 256
	switch codec {
	case opus:
		a.depacket = &codecs.OpusPacket{}
		rate, window = 48000, 16
	case vp8:
		a.depacket = &vp8Frame{}
	case h264:
		a.depacket = &codecs.H264Packet{}
	}
	a.builder = samplebuilder.New(window, a.depacket, rate, samplebuilder.WithPacketDroppedHandler(func() {
		a.loss()
	}))
	return a
}

func (a *assembler) loss() {
	if a.codec != opus {
		a.needKey = true
		a.pending = nil
		if a.codec == h264 {
			// Clear an unfinished FU-A so it cannot leak into the next IDR.
			*a.depacket.(*codecs.H264Packet) = codecs.H264Packet{}
		}
	}
}

func (a *assembler) requestIfNeeded() {
	if a.needKey && time.Since(a.lastPLI) >= time.Second {
		a.lastPLI = time.Now()
		a.request()
	}
}

func (a *assembler) push(p *rtp.Packet) {
	a.builder.Push(p)
	a.drain(false)
}

func (a *assembler) drain(force bool) {
	next := a.builder.PopPackets
	if force {
		next = a.builder.ForcePopPackets
	}
	for packets := next(); len(packets) != 0; packets = next() {
		a.sample(packets)
	}
	a.requestIfNeeded()
}

func (a *assembler) sample(packets []*rtp.Packet) {
	first, last := packets[0], packets[len(packets)-1]
	ts := first.Timestamp
	if a.seen && first.SequenceNumber != a.seq+1 {
		a.loss()
	}
	a.seen, a.seq = true, last.SequenceNumber
	if len(first.Payload) == 0 || (a.hasTS && int32(ts-a.lastTS) <= 0) {
		return // padding, duplicate or reordered old access unit
	}
	if a.codec == opus {
		if len(packets) == 1 {
			a.lastTS, a.hasTS = ts, true
			a.onFrame(ts, true, first.Payload)
		}
		return
	}
	if !a.depacket.IsPartitionHead(first.Payload) || (a.codec == vp8 && !last.Marker) {
		a.loss()
		return
	}
	var frame []byte
	if a.codec == h264 && len(a.pending) != 0 {
		if a.pendingTS == ts {
			frame, a.pending = a.pending, nil
		} else {
			a.loss()
		}
	}
	for i, p := range packets {
		if p.Timestamp != ts || p.SequenceNumber != first.SequenceNumber+uint16(i) {
			a.loss()
			return
		}
		payload, err := a.depacket.Unmarshal(p.Payload)
		if err != nil || len(frame)+len(payload) > maxFrame {
			a.loss()
			return
		}
		frame = append(frame, payload...)
	}
	key := false
	if a.codec == vp8 {
		if len(frame) < 3 {
			a.loss()
			return
		}
		key = frame[0]&1 == 0 && len(frame) >= 10 && bytes.Equal(frame[3:6], []byte{0x9d, 0x01, 0x2a})
	} else {
		if !last.Marker {
			a.pending, a.pendingTS = frame, ts
			return
		}
		frame, key = a.annexB(frame)
	}
	if len(frame) == 0 || (a.needKey && !key) {
		return
	}
	a.needKey, a.hasTS, a.lastTS = false, true, ts
	a.onFrame(ts, key, frame)
}

var startCode = []byte{0, 0, 0, 1} // Pion's H.264 depacketizer emits four-byte Annex B prefixes.

func (a *assembler) annexB(frame []byte) ([]byte, bool) {
	key, picture, sps, pps := false, false, false, false
	for rest := frame; len(rest) > 4 && bytes.HasPrefix(rest, startCode); {
		n := bytes.Index(rest[4:], startCode)
		if n < 0 {
			n = len(rest)
		} else {
			n += 4
		}
		switch rest[4] & 31 {
		case 1, 2, 3, 4:
			picture = true
		case 5:
			key, picture = true, true
		case 7:
			if n <= 32768 {
				a.sps = append(a.sps[:0], rest[:n]...)
				sps = true
			}
		case 8:
			if n <= 32768 {
				a.pps = append(a.pps[:0], rest[:n]...)
				pps = true
			}
		}
		rest = rest[n:]
	}
	if !picture || key && (len(a.sps) == 0 || len(a.pps) == 0) {
		return nil, false
	}
	if key && (!sps || !pps) {
		if len(a.sps)+len(a.pps)+len(frame) > maxFrame {
			return nil, false
		}
		b := make([]byte, 0, len(a.sps)+len(a.pps)+len(frame))
		b = append(b, a.sps...)
		b = append(b, a.pps...)
		frame = append(b, frame...)
	}
	return frame, key
}
