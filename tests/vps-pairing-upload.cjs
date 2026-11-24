'use strict';
// Optional real multipart request through PHP's upload parser, against the
// packaged gateway with a disposable SQLite school and synthetic credentials.
const test = require('node:test');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const wasm = process.env.MPM_TEST_PHP_WASM;

test('packaged Manager accepts a real private multipart pairing only from an authorized idle school admin', {skip: !wasm}, async () => {
    const {PHP} = require(path.join(wasm, 'node_modules/@php-wasm/universal'));
    const {loadNodeRuntime, createNodeFsMountHandler} = require(path.join(wasm, 'node_modules/@php-wasm/node'));
    const php = new PHP(await loadNodeRuntime('8.3', {emscriptenOptions: {processId: 1}}));
    const site = 'https://qa-school.example.test';
    const sitePath = '/campus';
    const slug = 'school-classroom';
    const pair = {format:'baci-vps-pairing/1', site:site + sitePath, signal_url:'wss://qa-media.example.test',
        api_key:'a'.repeat(24), api_secret:'b'.repeat(64), recording_secret:'c'.repeat(64)};
    try {
        php.mkdir('/repo');
        await php.mount('/repo', createNodeFsMountHandler(root));
        const gateway = execFileSync('unzip', ['-p', path.join(root, 'artifacts/baci-plugin-manager-update-v0.1.31.zip'), 'plugins/school-plugins.php']);
        php.mkdir('/qa/public/campus/plugins', {recursive: true});
        php.writeFile('/qa/public/campus/plugins/school-plugins.php', gateway);
        const fixture = `<?php
require '/qa/public/campus/plugins/school-plugins.php';
$_SERVER['DOCUMENT_ROOT'] = '/qa/public';
$_SERVER['HTTPS'] = 'on';
$_SERVER['HTTP_HOST'] = 'qa-school.example.test';
$_SERVER['SCRIPT_NAME'] = '/campus/school-classroom-settings.php';
$GLOBALS['MPM_CLI_SITE_PATH'] = '/campus';
$origin = mpm_origin();
$db = new PDO('sqlite:/qa/fixture.db', null, null, [PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION]);
$site = mpm_site($db);
$slug = 'school-classroom';
if (!$site) {
    $db->exec("CREATE TABLE admins(id INT PRIMARY KEY,role TEXT,status INT);\n
        INSERT INTO admins VALUES(1,'super_admin',1),(2,'admin',1),(3,'super_admin',0)");
    $site = mpm_initialize($db, $origin, '/qa/private');
    mpm_install($db, $site['private_dir'], '/repo/artifacts/school-classroom-plugin-v0.4.24.zip');
    $row = mpm_row($db, $slug);
    mpm_write_settings($db, $site['private_dir'], $row, ['record_lessons'=>false,'notify_school_bale'=>true,'attendance_min_minutes'=>5]);
    echo 'fixture ready';
    return;
}
$_SESSION = ['admin_id'=>(int)($_SERVER['HTTP_X_FIXTURE_ADMIN'] ?? 0)];
if (!mpm_is_manager($db)) { http_response_code(403); return; }
try {
    $message = mpm_post($db, $site, $origin, 'fixture-csrf');
    $row = mpm_row($db, $slug);
    $cfg = mpm_settings($db, $site['private_dir'], $row);
    $saved = mpm_owned_row($db, $row)['settings'];
    echo json_encode(['message'=>$message,'state'=>$row['state'],'connection'=>hash('sha256',$cfg['api_secret']),
        'paired'=>$cfg['recording_secret'] !== $cfg['api_secret'] && !str_contains($saved,$cfg['api_secret']),
        'keep'=>$cfg['record_lessons'] === false && $cfg['notify_school_bale'] === true && $cfg['attendance_min_minutes'] === 5]);
} catch(MpmNotice $error) {
    if (http_response_code() < 400) http_response_code(400);
    echo $error->getMessage();
}
`;
        php.writeFile('/qa/public/campus/pair-fixture.php', fixture);
        const opts = {scriptPath:'/qa/public/campus/pair-fixture.php', relativeUri:'/campus/pair-fixture.php', usePrimaryPhp:true};
        assert.match((await php.run(opts)).text, /fixture ready/);
        const upload = ({admin=1, csrf='fixture-csrf', origin=site, confirmation='yes', file=pair} = {}) => {
            const boundary = 'baci-offline-synthetic-multipart';
            const fields = {csrf, action:'pair-vps', slug, confirm_vps:confirmation};
            const body = Object.entries(fields).map(([k,v])=>`--${boundary}\r\nContent-Disposition: form-data; name="${k}"\r\n\r\n${v}\r\n`).join('') +
                `--${boundary}\r\nContent-Disposition: form-data; name="vps_pair"; filename="Baci-VPS-pairing.json"\r\nContent-Type: application/json\r\n\r\n${JSON.stringify(file)}\r\n--${boundary}--\r\n`;
            return php.run({...opts, method:'POST', headers:{'content-type':`multipart/form-data; boundary=${boundary}`, origin,'x-fixture-admin':String(admin)}, body:Buffer.from(body)});
        };
        for (const bad of [{admin:2}, {admin:3}, {csrf:'forged'}, {origin:'https://elsewhere.example.test'}]) {
            assert.equal((await upload(bad)).httpStatusCode, 403);
        }
        for (const bad of [{confirmation:''}, {file:{...pair, site}}, {file:{...pair, recording_secret:pair.api_secret}}]) {
            const response = await upload(bad);
            assert.equal(response.httpStatusCode, 400);
            assert.ok(!response.text.includes(pair.api_secret));
        }
        const success = await upload();
        assert.equal(success.httpStatusCode, 200);
        const result = JSON.parse(success.text);
        assert.equal(result.state, 'disabled');
        assert.equal(result.connection, require('node:crypto').createHash('sha256').update(pair.api_secret).digest('hex'));
        assert.ok(result.paired && result.keep);
        assert.ok(!success.text.includes(pair.api_secret));
        assert.match(JSON.parse((await upload()).text).message, /از قبل/);
    } finally {
        await php.exit();
    }
});
