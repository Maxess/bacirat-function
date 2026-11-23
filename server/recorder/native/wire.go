package main

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"os"
	"sync/atomic"
)

const (
	protocol    = "baci-rtp/2"
	data        = 1
	ready       = 2
	done        = 3
	maxFrame    = 1 << 20
	batch       = 64 << 10
	queueBytes  = 4 << 20
	queueBlocks = 64
)

var errPressure = errors.New("encoded receiver queue full")

type message struct {
	kind byte
	body []byte
}

// Only the writer goroutine deals with the pipe/OS. The receiver never waits
// for an IPC write or for disk. Count the active buffer against the budget too.
type queue struct {
	messages chan message
	finished chan error
	pending  atomic.Int64
	blocks   atomic.Int32
	noted    bool // producer only
}

func newQueue(out io.Writer, failed func(error)) *queue {
	q := &queue{messages: make(chan message, queueBlocks), finished: make(chan error, 1)}
	go func() {
		for msg := range q.messages {
			var header [5]byte
			header[0] = msg.kind
			binary.BigEndian.PutUint32(header[1:], uint32(len(msg.body)))
			err := writeAll(out, header[:])
			if err == nil && len(msg.body) != 0 {
				err = writeAll(out, msg.body)
			}
			if err != nil {
				q.finished <- err
				failed(err)
				return
			}
			q.pending.Add(-int64(cap(msg.body)))
			q.blocks.Add(-1)
		}
		q.finished <- nil
	}()
	return q
}

func writeAll(w io.Writer, b []byte) error {
	n, err := w.Write(b)
	if err == nil && n != len(b) {
		return io.ErrShortWrite
	}
	return err
}

// All enqueue calls are serialized with the receiver's small event-order lock;
// this never holds on disk or on a pipe. Ownership of body transfers to q.
func (q *queue) put(kind byte, body []byte) error {
	size, blocks := q.pending.Load()+int64(cap(body)), q.blocks.Load()+1
	if !q.noted && (size >= queueBytes/2 || blocks >= queueBlocks/2) {
		q.noted = true
		go func() { _, _ = io.WriteString(os.Stderr, "recorder: native IPC queue under pressure\n") }()
	}
	if size > queueBytes || blocks > queueBlocks {
		return errPressure
	}
	q.pending.Add(int64(cap(body)))
	q.blocks.Add(1)
	select {
	case q.messages <- message{kind, body}:
		return nil
	default:
		q.pending.Add(-int64(cap(body)))
		q.blocks.Add(-1)
		return errPressure
	}
}

// The recorder owns the state directory and the disk quota. This child only
// submits framed records to its parent, never arbitrary files.
type pipe struct {
	out *queue
	buf bytes.Buffer
	err error
}

func (p *pipe) message(kind byte, b []byte) {
	if p.err == nil {
		p.err = p.out.put(kind, b)
	}
}

func (p *pipe) flush() {
	if p.buf.Len() != 0 {
		p.message(data, p.buf.Bytes())
		p.buf = bytes.Buffer{} // don't overwrite the in-flight batch
	}
}

func (p *pipe) record(header []byte, payload []byte) {
	if p.err != nil {
		return
	}
	if len(header) == 0 || len(header) > 512 || len(payload) > maxFrame {
		p.err = errors.New("bounds")
		return
	}
	var record [12]byte // receipt time filled in by the parent before disk queuing
	binary.BigEndian.PutUint32(record[4:], uint32(len(header)))
	binary.BigEndian.PutUint32(record[8:], uint32(len(payload)))
	p.buf.Write(record[:])
	p.buf.Write(header)
	p.buf.Write(payload)
	if p.buf.Len() >= batch {
		p.flush()
	}
}

func (p *pipe) event(fields ...any) {
	header, err := json.Marshal(fields)
	if err != nil {
		p.err = err
		return
	}
	p.record(header, nil)
}
