package group

// WhiteboardStroke is a single stroke on the whiteboard.  Points are
// [x, y] pairs in normalised coordinates (0..1) so that the board
// scales to any canvas size.  Strokes are appended to incrementally,
// which allows real-time drawing: a client sends small batches of
// points sharing the same stroke id.
type WhiteboardStroke struct {
	Id     string      `json:"id"`
	Color  string      `json:"color"`
	Width  float64     `json:"width"`
	Points [][]float64 `json:"points"`
}

// maxWhiteboardStrokes bounds the amount of state kept per group so
// that a runaway client cannot exhaust memory.
const maxWhiteboardStrokes = 500

// AddWhiteboardStroke appends points to the stroke with the given id,
// creating it if necessary.  It returns the full stroke.
func (g *Group) AddWhiteboardStroke(s WhiteboardStroke) WhiteboardStroke {
	g.mu.Lock()
	defer g.mu.Unlock()

	for i := range g.whiteboard {
		if g.whiteboard[i].Id == s.Id {
			pts := g.whiteboard[i].Points
			// Consecutive batches repeat the last point of the
			// previous one, so that the receiver can draw the
			// connecting segment.  Drop it when accumulating, so
			// that the stored stroke has no duplicate.
			if len(pts) > 0 && len(s.Points) >= 1 &&
				pts[len(pts)-1][0] == s.Points[0][0] &&
				pts[len(pts)-1][1] == s.Points[0][1] {
				s.Points = s.Points[1:]
			}
			g.whiteboard[i].Points = append(
				g.whiteboard[i].Points, s.Points...)
			return g.whiteboard[i]
		}
	}

	if len(g.whiteboard) >= maxWhiteboardStrokes {
		// drop the oldest stroke to bound memory usage
		copy(g.whiteboard, g.whiteboard[1:])
		g.whiteboard = g.whiteboard[:len(g.whiteboard)-1]
	}

	g.whiteboard = append(g.whiteboard, s)
	return s
}

// GetWhiteboard returns a copy of the current whiteboard state.
func (g *Group) GetWhiteboard() []WhiteboardStroke {
	g.mu.Lock()
	defer g.mu.Unlock()

	out := make([]WhiteboardStroke, len(g.whiteboard))
	copy(out, g.whiteboard)
	return out
}

// ClearWhiteboard removes all strokes from the whiteboard.
func (g *Group) ClearWhiteboard() {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.whiteboard = nil
}
