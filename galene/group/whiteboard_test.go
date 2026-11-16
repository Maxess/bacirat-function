package group

import "testing"

func TestAddWhiteboardStroke(t *testing.T) {
	g := &Group{name: "test"}

	// The first batch of a stroke is stored as it is.
	g.AddWhiteboardStroke(WhiteboardStroke{
		Id:     "a",
		Points: [][]float64{{0, 0}},
	})
	if len(g.whiteboard) != 1 || len(g.whiteboard[0].Points) != 1 {
		t.Fatalf("expected 1 stroke with 1 point, got %v", g.whiteboard)
	}

	// The next batch repeats the point the previous one ended on, so
	// that the receiver can draw the connecting segment.  The
	// duplicate must not be stored.
	g.AddWhiteboardStroke(WhiteboardStroke{
		Id:     "a",
		Points: [][]float64{{0, 0}, {1, 1}},
	})
	if len(g.whiteboard) != 1 || len(g.whiteboard[0].Points) != 2 {
		t.Fatalf("expected 1 stroke with 2 points, got %v", g.whiteboard)
	}

	// A different id starts a new stroke.
	g.AddWhiteboardStroke(WhiteboardStroke{
		Id:     "b",
		Points: [][]float64{{2, 2}},
	})
	if len(g.whiteboard) != 2 {
		t.Fatalf("expected 2 strokes, got %v", g.whiteboard)
	}

	// The stored state is a faithful copy of what was drawn.
	if got := g.GetWhiteboard(); len(got) != 2 ||
		len(got[0].Points) != 2 || len(got[1].Points) != 1 {
		t.Fatalf("unexpected whiteboard state: %v", got)
	}

	g.ClearWhiteboard()
	if got := g.GetWhiteboard(); len(got) != 0 {
		t.Fatalf("expected an empty whiteboard, got %v", got)
	}
}

func TestAddWhiteboardStrokeBound(t *testing.T) {
	g := &Group{name: "test"}
	for i := 0; i < maxWhiteboardStrokes+10; i++ {
		g.AddWhiteboardStroke(WhiteboardStroke{
			Id:     string(rune('a' + i%26)) + string(rune('0'+i/26)),
			Points: [][]float64{{float64(i), float64(i)}},
		})
	}
	if len(g.whiteboard) != maxWhiteboardStrokes {
		t.Errorf("expected at most %v strokes, got %v",
			maxWhiteboardStrokes, len(g.whiteboard))
	}
}

func TestCleanGroupName(t *testing.T) {
	for _, tc := range []struct {
		in, out string
	}{
		{"math-101", "math-101"},
		{"../etc/passwd", "etc/passwd"},
		{"abs/", "abs"},
		{"a/../b", "b"},
		{"a/b", "a/b"},
	} {
		if got := cleanGroupName(tc.in); got != tc.out {
			t.Errorf("cleanGroupName(%q) = %q, want %q",
				tc.in, got, tc.out)
		}
	}
}
