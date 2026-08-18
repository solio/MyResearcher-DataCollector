"""Shared public-DOM JavaScript for Xueqiu browser runtimes.

Keep selectors and challenge classification in one place.  Both the legacy
Apple Events adapter and the dedicated Chrome/CDP adapter consume these
scripts; neither script reads cookies, storage, credentials, or private APIs.
"""

from __future__ import annotations


CHALLENGE_TOKENS = (
    "md5__1038",
    "验证码",
    "安全验证",
    "访问验证",
    "captcha",
    "人机验证",
)


PAGE_STATE_JS = r'''
JSON.stringify({
  url: location.href,
  title: document.title,
  readyState: document.readyState,
  posts: document.querySelectorAll('article.timeline__item').length,
  challenge: ['md5__1038', '验证码', '安全验证', '访问验证', 'captcha', '人机验证']
    .filter(token => (location.href + '\n' + document.title + '\n' +
      (document.body ? document.body.innerText : '')).toLowerCase()
      .includes(token.toLowerCase()))
})
'''


ACTIVE_PAGE_JS = r'''
(() => {
  const active = document.querySelector(
    ".pagination .active, .pagination li.active, [aria-current='page']"
  );
  const parsed = Number.parseInt(active ? active.textContent.trim() : '1', 10);
  return Number.isFinite(parsed) && parsed > 0 ? String(parsed) : '1';
})()
'''


READ_PAGE_JS = r'''
JSON.stringify(Array.from(document.querySelectorAll('article.timeline__item')).map(node => {
  const source = node.querySelector('a.date-and-source');
  const user = node.querySelector('a.user-name');
  const body = node.querySelector('.timeline__item__content');
  const text = selector => {
    const value = node.querySelector(selector);
    return value ? (value.innerText || value.textContent || '').trim() : null;
  };
  const count = name => {
    const value = node.querySelector(`[data-${name}], .${name}`);
    const raw = value ? (value.getAttribute(`data-${name}`) || value.innerText || '') : '';
    const parsed = Number.parseInt(raw.replace(/[^0-9]/g, ''), 10);
    return Number.isFinite(parsed) ? parsed : null;
  };
  return {
    status_id: source && (source.getAttribute('data-id') || source.dataset.id),
    author_id: user && (user.getAttribute('data-user-id') || user.dataset.userId),
    author_name: user ? (user.innerText || user.textContent || '').trim() : null,
    url: source && source.href,
    content: body ? (body.innerText || body.textContent || '').trim() : '',
    title: text('.timeline__item__title'),
    time_text_observed: source ? (source.innerText || source.textContent || '').trim() : '',
    read_count: count('read-count'),
    reply_count: count('reply-count'),
    like_count: count('like-count'),
    forward_count: count('forward-count')
  };
}))
'''


CLICK_PAGE_JS = r'''
(() => {
  const wanted = String(%d);
  const candidates = Array.from(document.querySelectorAll(
    '.pagination a, .pagination button, .pagination li, [aria-label]'
  ));
  const control = candidates.find(node => (node.textContent || '').trim() === wanted);
  if (!control) return JSON.stringify({clicked: false});
  const clickable = control.matches('a,button') ? control : control.querySelector('a,button') || control;
  clickable.click();
  return JSON.stringify({clicked: true});
})()
'''


DETAIL_STATE_JS = r'''
(() => {
  const status = window.SNOWMAN_STATUS || null;
  let embeddedStatusJson = null;
  if (!status) {
    const marker = 'window.SNOWMAN_STATUS =';
    const script = Array.from(document.scripts).find(node =>
      (node.textContent || '').includes(marker)
    );
    if (script) {
      const text = script.textContent || '';
      const start = text.indexOf(marker) + marker.length;
      const remainder = text.slice(start).trim();
      const targetAssignment = ';\\nwindow.SNOWMAN_TARGET =';
      const targetStart = remainder.indexOf(targetAssignment);
      embeddedStatusJson = targetStart >= 0
        ? remainder.slice(0, targetStart).trim()
        : remainder.replace(/;\s*$/, '');
    }
  }
  return JSON.stringify({
    url: location.href,
    title: document.title,
    readyState: document.readyState,
    challenge: ['md5__1038', '验证码', '安全验证', '访问验证', 'captcha', '人机验证']
      .filter(token => (location.href + '\n' + document.title + '\n' +
        (document.body ? document.body.innerText : '')).toLowerCase()
        .includes(token.toLowerCase())),
    status,
    embeddedStatusJson
  });
})()
'''


def clean_embedded_status_json(value: str) -> str:
    """Remove the observed Snowman assignment that follows the JSON object."""
    remainder = value.strip()
    target_assignment = ";\nwindow.SNOWMAN_TARGET ="
    target_start = remainder.find(target_assignment)
    if target_start >= 0:
        remainder = remainder[:target_start].rstrip()
    return remainder.rstrip(";").rstrip()
