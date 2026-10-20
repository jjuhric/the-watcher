// Top-right menu shared by every page. To add an item later, add a line to ITEMS.
(function () {
  const ITEMS = [
    { label: "Dashboard", href: "/" },
    { label: "Change Username / Password", href: "/account" },
  ];
  const here = location.pathname;
  const bar = document.createElement("div");
  bar.className = "menu";
  bar.innerHTML =
    '<button class="menu-btn" aria-label="Menu" aria-haspopup="true" aria-expanded="false">' +
    '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h16M4 12h16M4 17h16"/></svg></button>' +
    '<nav class="menu-list" hidden>' +
    ITEMS.map(i => `<a href="${i.href}"${i.href === here ? ' class="on"' : ""}>${i.label}</a>`).join("") +
    "</nav>";
  document.body.appendChild(bar);
  const btn = bar.querySelector("button"), list = bar.querySelector("nav");
  const set = open => { list.hidden = !open; btn.setAttribute("aria-expanded", open); };
  btn.onclick = e => { e.stopPropagation(); set(list.hidden); };
  document.addEventListener("click", () => set(false));
  document.addEventListener("keydown", e => { if (e.key === "Escape") set(false); });
})();
