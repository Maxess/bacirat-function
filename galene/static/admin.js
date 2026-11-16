// Administration panel.
//
// Plain JavaScript on top of management.js: no framework, no build
// step, so it loads instantly even on very small servers.
// Authentication relies on the browser's own HTTP Basic dialog, which
// means no credentials are ever stored in the page.

'use strict';

function adminEl(id) {
    return document.getElementById(id);
}

function showError(message) {
    let elt = adminEl('admin-error');
    if(!message) {
        elt.classList.add('invisible');
        return;
    }
    elt.textContent = message;
    elt.classList.remove('invisible');
}

async function adminFetch(url, options) {
    let r = await fetch(url, options);
    if(!r.ok)
        throw httpError(r);
    return r;
}

async function adminFetchJSON(url, options) {
    let r = await adminFetch(url, options);
    return await r.json();
}

async function adminFetchText(url, options) {
    let r = await adminFetch(url, options);
    return await r.text();
}

/** Formats a duration in seconds as h:mm or m:ss. */
function formatDuration(seconds) {
    if(seconds < 3600)
        return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

/**
 * Formats a user's permissions.  The server sends either a role name
 * (a string) or an explicit list of permissions (an array).
 *
 * @param {string|Array<string>|undefined} permissions
 * @returns {string}
 */
function formatPermissions(permissions) {
    if(!permissions)
        return '';
    if(Array.isArray(permissions))
        return permissions.join(', ');
    return String(permissions);
}

// ---------------------------------------------------------------- tabs

function selectTab(name) {
    for(let b of document.querySelectorAll('.admin-nav button')) {
        b.classList.toggle('active', b.dataset.tab === name);
    }
    for(let s of document.querySelectorAll('.admin-tab')) {
        s.classList.toggle('invisible', s.id !== 'tab-' + name);
    }
}

// ------------------------------------------------------------ branding

async function loadBranding() {
    // brand.js applies the colours and the favicon; the panel only
    // needs to show the site name in its own heading.
    let b = await applyBranding();
    if(b && b.siteName)
        adminEl('admin-title').textContent = `${b.siteName} — Administration`;
}

async function loadBrandingForm() {
    try {
        let b = await adminFetchJSON('/galene-api/v0/.branding');
        adminEl('branding-siteName').value = b.siteName || '';
        adminEl('branding-tagline').value = b.tagline || '';
        adminEl('branding-logoURL').value = b.logoURL || '';
        adminEl('branding-faviconURL').value = b.faviconURL || '';
        adminEl('branding-primaryColor').value = b.primaryColor || '#2e7d32';
    } catch(e) {
        showError('Could not load branding: ' + e.message);
    }
}

// ------------------------------------------------------------- classes

let currentClass = null;

async function refreshClasses() {
    try {
        let groups = await listGroups();
        let tbody = adminEl('classes-list');
        tbody.textContent = '';
        for(let g of groups) {
            let tr = document.createElement('tr');

            let tdName = document.createElement('td');
            let link = document.createElement('a');
            link.textContent = g;
            link.href = `/group/${encodeURIComponent(g)}/`;
            tdName.appendChild(link);
            tr.appendChild(tdName);

            let tdTitle = document.createElement('td');
            try {
                let desc = await getGroup(g);
                tdTitle.textContent = desc.data.displayName || '';
            } catch(e) {
                tdTitle.textContent = '';
            }
            tr.appendChild(tdTitle);

            let tdUsers = document.createElement('td');
            try {
                let users = await listUsers(g);
                tdUsers.textContent = String(users.length);
            } catch(e) {
                tdUsers.textContent = '?';
            }
            tr.appendChild(tdUsers);

            let tdActions = document.createElement('td');

            let open = document.createElement('button');
            open.textContent = 'Manage';
            open.addEventListener('click', () => openClass(g));
            tdActions.appendChild(open);

            let del = document.createElement('button');
            del.textContent = 'Delete';
            del.addEventListener('click', async () => {
                if(!confirm(`Delete class ${g}?`))
                    return;
                try {
                    let desc = await getGroup(g);
                    await deleteGroup(g, desc.etag);
                    showError(null);
                    await refreshClasses();
                } catch(e) {
                    showError(e.message);
                }
            });
            tdActions.appendChild(del);

            tr.appendChild(tdActions);
            tbody.appendChild(tr);
        }
    } catch(e) {
        showError('Could not list classes: ' + e.message);
    }
}

async function openClass(name) {
    currentClass = name;
    adminEl('class-details').classList.remove('invisible');
    adminEl('class-details-title').textContent = 'Class: ' + name;
    try {
        let desc = await getGroup(name);
        adminEl('class-displayName').value = desc.data.displayName || '';
        adminEl('class-description').value = desc.data.description || '';
        adminEl('class-max-clients').value = desc.data['max-clients'] || 0;
        adminEl('class-public').checked = !!desc.data.public;
        adminEl('class-recording').checked = !!desc.data['allow-recording'];
    } catch(e) {
        showError(e.message);
    }
    await refreshUsers();
}

async function refreshUsers() {
    if(!currentClass)
        return;
    try {
        let users = await listUsers(currentClass);
        let tbody = adminEl('users-list');
        tbody.textContent = '';
        for(let u of users) {
            let tr = document.createElement('tr');

            let tdName = document.createElement('td');
            tdName.textContent = u;
            tr.appendChild(tdName);

            let tdRole = document.createElement('td');
            try {
                let d = await getUser(currentClass, u, false);
                tdRole.textContent = formatPermissions(d.data.permissions);
            } catch(e) {
                tdRole.textContent = '?';
            }
            tr.appendChild(tdRole);

            let tdActions = document.createElement('td');

            let teacher = document.createElement('button');
            teacher.textContent = 'Make teacher';
            teacher.addEventListener('click', async () => {
                try {
                    await updateUser(currentClass, u, false,
                                     {permissions: 'teacher'});
                    showError(null);
                    await refreshUsers();
                } catch(e) {
                    showError(e.message);
                }
            });
            tdActions.appendChild(teacher);

            let student = document.createElement('button');
            student.textContent = 'Make student';
            student.addEventListener('click', async () => {
                try {
                    await updateUser(currentClass, u, false,
                                     {permissions: 'student'});
                    showError(null);
                    await refreshUsers();
                } catch(e) {
                    showError(e.message);
                }
            });
            tdActions.appendChild(student);

            let del = document.createElement('button');
            del.textContent = 'Delete';
            del.addEventListener('click', async () => {
                if(!confirm(`Delete user ${u}?`))
                    return;
                try {
                    let d = await getUser(currentClass, u, false);
                    await deleteUser(currentClass, u, false, d.etag);
                    showError(null);
                    await refreshUsers();
                } catch(e) {
                    showError(e.message);
                }
            });
            tdActions.appendChild(del);

            tr.appendChild(tdActions);
            tbody.appendChild(tr);
        }
    } catch(e) {
        showError('Could not list users: ' + e.message);
    }
}

// ---------------------------------------------------------- attendance

let attendanceRows = [];

async function refreshAttendance() {
    let g = adminEl('attendance-class').value;
    let date = adminEl('attendance-date').value;
    if(!g)
        return;
    try {
        let url = `/galene-api/v0/.attendance/${encodeURIComponent(g)}` +
            (date ? `?date=${encodeURIComponent(date)}` : '');
        let records = await adminFetchJSON(url);
        attendanceRows = records;

        let tbody = adminEl('attendance-list');
        tbody.textContent = '';
        for(let r of records) {
            let tr = document.createElement('tr');

            let tdName = document.createElement('td');
            tdName.textContent = r.username;
            tr.appendChild(tdName);

            let tdJoins = document.createElement('td');
            tdJoins.textContent = (r.joins || []).map(
                t => new Date(t).toLocaleTimeString()).join(', ');
            tr.appendChild(tdJoins);

            let tdLeaves = document.createElement('td');
            tdLeaves.textContent = (r.leaves || []).map(
                t => new Date(t).toLocaleTimeString()).join(', ');
            tr.appendChild(tdLeaves);

            let tdTotal = document.createElement('td');
            tdTotal.textContent = formatDuration(r.totalSeconds || 0);
            tr.appendChild(tdTotal);

            tbody.appendChild(tr);
        }

        let empty = records.length === 0;
        adminEl('attendance-empty').classList.toggle('invisible', !empty);
        adminEl('attendance-csv').classList.toggle('invisible', empty);
    } catch(e) {
        showError('Could not load attendance: ' + e.message);
    }
}

function downloadCSV() {
    let rows = [['username', 'joins', 'leaves', 'totalSeconds']];
    for(let r of attendanceRows) {
        rows.push([
            r.username,
            (r.joins || []).join(' '),
            (r.leaves || []).join(' '),
            String(r.totalSeconds || 0),
        ]);
    }
    let csv = rows.map(
        r => r.map(c => `"${String(c).replace(/"/g, '""')}"`).join(',')
    ).join('\n');
    let a = adminEl('attendance-csv');
    a.href = URL.createObjectURL(new Blob([csv], {type: 'text/csv'}));
}

// ---------------------------------------------------------- whiteboard

async function refreshWhiteboard() {
    let g = adminEl('whiteboard-class').value;
    if(!g)
        return;
    try {
        let strokes = await adminFetchJSON(
            `/galene-api/v0/.whiteboard/${encodeURIComponent(g)}`);
        adminEl('whiteboard-info').textContent =
            `${strokes.length} stroke(s) on the whiteboard of ${g}.`;
    } catch(e) {
        showError('Could not load whiteboard: ' + e.message);
    }
}

async function clearWhiteboard() {
    let g = adminEl('whiteboard-class').value;
    if(!g)
        return;
    if(!confirm(`Clear the whiteboard of ${g}?`))
        return;
    try {
        await adminFetch(
            `/galene-api/v0/.whiteboard/${encodeURIComponent(g)}`,
            {method: 'DELETE'});
        showError(null);
        await refreshWhiteboard();
    } catch(e) {
        showError(e.message);
    }
}

/** Populates the class selectors used by the attendance and whiteboard tabs. */
async function refreshClassSelectors() {
    try {
        let groups = await listGroups();
        for(let id of ['attendance-class', 'whiteboard-class']) {
            let sel = adminEl(id);
            let previous = sel.value;
            sel.textContent = '';
            for(let g of groups) {
                let o = document.createElement('option');
                o.value = g;
                o.textContent = g;
                sel.appendChild(o);
            }
            if(groups.includes(previous))
                sel.value = previous;
        }
    } catch(e) {
        showError('Could not list classes: ' + e.message);
    }
}

// ----------------------------------------------------------------- api

function showAPIExample() {
    let base = `${location.origin}/api/v1/groups`;
    let example = `GET  ${base}
     -> ["math-101", "physics-202"]

POST ${base}/math-101/sync
Content-Type: application/json

{
  "displayName": "Mathematics 101",
  "description": "Monday morning class",
  "users": [
    {"username": "teacher", "password": "secret", "permissions": "teacher"},
    {"username": "alice",   "password": "secret", "permissions": "student"}
  ]
}

GET  ${location.origin}/galene-api/v0/.attendance/math-101?date=2024-05-01
     -> [{"username": "alice", "joins": [...], "totalSeconds": 3120}]
`;
    adminEl('api-example').textContent = example;
}

// ---------------------------------------------------------------- init

async function init() {
    for(let b of document.querySelectorAll('.admin-nav button')) {
        b.addEventListener('click', () => selectTab(b.dataset.tab));
    }

    adminEl('create-class-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        let name = adminEl('new-class-name').value.trim();
        let title = adminEl('new-class-title').value.trim();
        if(!name)
            return;
        try {
            await createGroup(name, title ? {displayName: title} : {});
            showError(null);
            adminEl('new-class-name').value = '';
            adminEl('new-class-title').value = '';
            await refreshClasses();
            await refreshClassSelectors();
        } catch(e) {
            showError(e.message);
        }
    });

    adminEl('class-details-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        if(!currentClass)
            return;
        try {
            await updateGroup(currentClass, {
                displayName: adminEl('class-displayName').value,
                description: adminEl('class-description').value,
                'max-clients': Number(adminEl('class-max-clients').value) || 0,
                public: adminEl('class-public').checked,
                'allow-recording': adminEl('class-recording').checked,
            });
            showError(null);
        } catch(e) {
            showError(e.message);
        }
    });

    adminEl('create-user-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        if(!currentClass)
            return;
        let user = adminEl('new-username').value.trim();
        let role = adminEl('new-user-role').value;
        let password = adminEl('new-user-password').value;
        if(!user)
            return;
        try {
            await createUser(currentClass, user, false,
                             {permissions: role});
            if(password)
                await setPassword(currentClass, user, false, password);
            showError(null);
            adminEl('new-username').value = '';
            adminEl('new-user-password').value = '';
            await refreshUsers();
        } catch(e) {
            showError(e.message);
        }
    });

    adminEl('attendance-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        await refreshAttendance();
    });
    adminEl('attendance-csv').addEventListener('click', downloadCSV);

    adminEl('whiteboard-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        await refreshWhiteboard();
    });
    adminEl('whiteboard-clear-btn').addEventListener('click', clearWhiteboard);

    adminEl('branding-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        try {
            await adminFetch('/galene-api/v0/.branding', {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    siteName: adminEl('branding-siteName').value,
                    tagline: adminEl('branding-tagline').value,
                    logoURL: adminEl('branding-logoURL').value,
                    faviconURL: adminEl('branding-faviconURL').value,
                    primaryColor: adminEl('branding-primaryColor').value,
                }),
            });
            showError(null);
            await loadBranding();
            alert('Branding saved. Reload the class pages to see it.');
        } catch(e) {
            showError(e.message);
        }
    });

    let today = new Date();
    adminEl('attendance-date').value =
        `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}` +
        `-${String(today.getDate()).padStart(2, '0')}`;

    await loadBranding();
    await loadBrandingForm();
    await refreshClasses();
    await refreshClassSelectors();
    showAPIExample();
}

init();
