// Synthetic PDF with differently sized pages and distinct fills.
function twoPageBoardPDF() {
    const page=(w,h,i)=>`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${w} ${h}] /Resources <<>> /Contents ${i} 0 R >>`;
    const stream=s=>`<< /Length ${s.length} >>\nstream\n${s}\nendstream`;
    const objects=['<< /Type /Catalog /Pages 2 0 R >>','<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>',
        page(480,300,5),page(260,380,6),stream('1 1 0 rg 0 0 480 300 re f'),stream('0 1 1 rg 0 0 260 380 re f')];
    let file='%PDF-1.4\n';
    const offsets=objects.map((obj,i)=>{const offset=file.length; file+=`${i+1} 0 obj\n${obj}\nendobj\n`; return offset;});
    return Buffer.from(`${file}xref\n0 7\n0000000000 65535 f \n${offsets.map(n=>`${String(n).padStart(10,'0')} 00000 n \n`).join('')}trailer\n<< /Size 7 /Root 1 0 R >>\nstartxref\n${file.length}\n%%EOF\n`);
}
module.exports = {twoPageBoardPDF};
