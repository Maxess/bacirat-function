// Tap the encoded receiver stream, before ordinary WebRTC decoding. Keep forwarding
// the original frame to libwebrtc: dropping it provokes continual keyframe requests.
// No media elements, canvas, AudioContext, or MediaRecorder are used in the live page.
'use strict';
const transforms = new Map();
let batch = [], bytes = 0, failed = false;
function flush() {
    if (batch.length) self.postMessage(batch, batch.map(packet => packet[5]));
    batch = []; bytes = 0;
}
function fault() {
    if (!failed) self.postMessage('fault');
    failed = true;
}
setInterval(flush, 120);
self.onmessage = ({data}) => {
    if (data === 'flush') { flush(); self.postMessage('flushed'); }
    else if (data.drop) transforms.delete(data.drop);
    else if (data.key) transforms.get(data.key)?.sendKeyFrameRequest?.().catch(() => {});
};
self.onrtctransform = ({transformer}) => {
    const {id, mime, video, origin} = transformer.options;
    if (video) {
        transforms.set(id, transformer);
        transformer.sendKeyFrameRequest?.().catch(() => {});
    }
    const skew = performance.timeOrigin - origin;
    transformer.readable.pipeThrough(new TransformStream({
        transform(frame, output) {
            if (!failed) {
                const meta = frame.getMetadata();
                const codec = (meta.mimeType || mime).toLowerCase();
                const received = meta.receiveTime ?? performance.now();
                const data = frame.data.slice(0);
                if (data.byteLength > 1024 * 1024) fault();
                else if (data.byteLength) {
                    batch.push([id, Math.max(0, Math.round(received + skew)), codec,
                        (meta.rtpTimestamp ?? frame.timestamp) >>> 0, frame.type === 'key', data]);
                    bytes += data.byteLength;
                    if (bytes >= 512 * 1024 || batch.length >= 768) flush();
                }
            }
            output.enqueue(frame);
        },
    })).pipeTo(transformer.writable).catch(() => { /* Receiver may be removed while a frame is in flight. */ });
};
