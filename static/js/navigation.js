const toggle = document.querySelector('.menu-toggle');
const menu = document.querySelector('#site-menu');
if (toggle && menu) {
  document.documentElement.classList.add('js');
  const setOpen = (open) => {
    toggle.setAttribute('aria-expanded', String(open));
    menu.classList.toggle('is-open', open);
  };
  toggle.addEventListener('click', () => {
    setOpen(toggle.getAttribute('aria-expanded') !== 'true');
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && menu.classList.contains('is-open')) {
      setOpen(false);
      toggle.focus();
    }
  });
}

// Links remain usable without JavaScript; enhanced tabs replace only the discussion panel.
let discussionRequest;
document.addEventListener("click", async (event) => {
  const link = event.target.closest("a[data-discussion-tab]");
  if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  if (discussionRequest) discussionRequest.abort();
  const controller = new AbortController();
  discussionRequest = controller;
  try {
    const response = await fetch(link.href, {signal: controller.signal});
    if (!response.ok) throw new Error("Tab request failed");
    const page = new DOMParser().parseFromString(await response.text(), "text/html");
    const panel = page.querySelector("#reviews");
    if (!panel) throw new Error("Missing discussion panel");
    document.querySelector("#reviews").replaceWith(panel);
    history.replaceState(null, "", link.href);
    panel.querySelector("[aria-current]").focus({preventScroll: true});
  } catch (error) {
    if (error.name !== "AbortError") window.location.assign(link.href);
  }
});
