// Top-right menu shared by every page. To add an item later, add a line to ITEMS.
(function () {
  const ITEMS = [
    { label: "Dashboard", href: "/" },
    { label: "Change Username / Password", href: "/account" },
    { label: "Sign out", action: "logout" },
  ];
  // an expired session sends any page back to the sign-in screen
  const _fetch = window.fetch;
  window.fetch = function () {
    return _fetch.apply(this, arguments).then(r => {
      if (r.status === 401 && location.pathname !== "/login") location.href = "/login?next=" + location.pathname;
      return r;
    });
  };
  const here = location.pathname;
  const bar = document.createElement("div");
  bar.className = "menu";
  bar.innerHTML =
    '<button class="menu-btn" aria-label="Menu" aria-haspopup="true" aria-expanded="false">' +
    '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h16M4 12h16M4 17h16"/></svg></button>' +
    '<nav class="menu-list" hidden>' +
    ITEMS.map(i => i.action
      ? `<a href="#" data-action="${i.action}">${i.label}</a>`
      : `<a href="${i.href}"${i.href === here ? ' class="on"' : ""}>${i.label}</a>`).join("") +
    "</nav>";
  document.body.appendChild(bar);
  const btn = bar.querySelector("button"), list = bar.querySelector("nav");
  const set = open => { list.hidden = !open; btn.setAttribute("aria-expanded", open); };
  btn.onclick = e => { e.stopPropagation(); set(list.hidden); };
  list.addEventListener("click", e => {
    const a = e.target.closest("a[data-action='logout']");
    if (!a) return;
    e.preventDefault();
    _fetch("/api/logout", { method: "POST" }).finally(() => (location.href = "/login"));
  });
  document.addEventListener("click", () => set(false));
  document.addEventListener("keydown", e => { if (e.key === "Escape") set(false); });
})();
