/* Deliberately ES5: a browser unable to parse the full classroom can still use the HTML link. */
(function () {
    'use strict';
    document.addEventListener('DOMContentLoaded', function () {
        var root = document.getElementById('classroom');
        if (!root || root.getAttribute('data-entry-ready') === 'yes') return;
        document.getElementById('waiting').setAttribute('data-wait', 'no');
        document.getElementById('wait-title').textContent = 'نمای کامل آماده نشد';
        document.getElementById('wait-message').textContent = 'اگر بارگذاری کامل نشد یا مرورگر قدیمی است، درس را در نمای سبک باز کن. (PAGE-ASSETS)';
    });
}());
