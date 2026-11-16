// Copyright (c) 2024.
//
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to deal
// in the Software without restriction, including without limitation the rights
// to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
// copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:
//
// The above copyright notice and this permission notice shall be included in
// all copies or substantial portions of the Software.
//
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
// IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
// FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT SHALL THE
// AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
// LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
// OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
// THE SOFTWARE.

'use strict';

/**
 * applyBranding fetches /branding.json and applies the site identity to
 * the current page.
 *
 * Branding is optional: if it cannot be fetched, or if nothing is
 * configured, the page keeps its default appearance.  A failure here
 * must never prevent a user from joining a class, so every error is
 * swallowed.
 *
 * @returns {Promise<Object|null>} the branding that was applied, if any
 */
async function applyBranding() {
    let branding;
    try {
        let r = await fetch('/branding.json');
        if(!r.ok)
            return null;
        branding = await r.json();
    } catch(e) {
        return null;
    }

    let name = branding.siteName;
    if(name) {
        // The branding element is the site identity in the sidebar or
        // on the home page; the class page sets its own title to the
        // class name, so it is left alone here.
        let header = document.querySelector('.galene-header, .navbar-brand');
        if(header && header.classList.contains('galene-header')) {
            if(branding.logoURL) {
                header.textContent = '';
                let img = document.createElement('img');
                img.id = 'brand-logo';
                img.src = branding.logoURL;
                img.alt = name;
                header.appendChild(img);
            } else {
                header.textContent = name;
            }
        } else if(header) {
            header.textContent = name;
        }
    }

    if(branding.primaryColor) {
        let root = document.documentElement;
        root.style.setProperty('--brand-primary', branding.primaryColor);
        root.style.setProperty('--brand-primary-dark', branding.primaryColor);
        root.style.setProperty('--admin-accent', branding.primaryColor);
    }

    if(branding.faviconURL) {
        let link = document.createElement('link');
        link.rel = 'icon';
        link.href = branding.faviconURL;
        document.head.appendChild(link);
    }

    return branding;
}
