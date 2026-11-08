// ShopWiz front end: wallet fan, search, the checkout terminal and receipt, and the card cheat sheet.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const money = (n) => "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");
const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;

const STORE_KEY = "shopwiz.v1";
let allCards = [];
let state = load();

function load() {
  try {
    const s = JSON.parse(localStorage.getItem(STORE_KEY) || localStorage.getItem("bestswipe.v1"));
    if (s && Array.isArray(s.cards)) return { rotating: [], portal: false, bonus: null, members: [], wishlist: [], budget: null, ...s };
  } catch {}
  return { cards: [], rotating: [], portal: false, bonus: null, members: [], wishlist: [], budget: null };
}
function save() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch {}
}

async function api(path, body) {
  const res = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Something went wrong. Try again.");
  return data;
}

/* Cards */
function issuer(id) {
  if (id === "amex-gold") return "amex-gold";
  for (const k of ["chase", "amex", "citi", "capital", "wells", "discover", "boa", "apple", "prime", "costco", "target"]) {
    if (id.startsWith(k)) return k;
  }
  return "other";
}
function cardEl(card, mini = false, style = "") {
  if (!card) return "";
  return `<div class="ccard issuer-${issuer(card.id)}${mini ? " mini" : ""}" title="${esc(card.name)}" style="${style}">
    <span class="chip"></span><span class="nm">${esc(card.name)}</span><span class="net">${esc(card.network)}</span></div>`;
}
const cardById = (id) => allCards.find((c) => c.id === id);

// The foil and glow follow the pointer on anything with --mx/--my.
document.addEventListener("pointermove", (e) => {
  document.body.style.setProperty("--gx", e.clientX + "px");
  document.body.style.setProperty("--gy", e.clientY + "px");
  const t = e.target.closest?.(".ccard, .product, .gtile");
  if (t) {
    const r = t.getBoundingClientRect();
    t.style.setProperty("--mx", ((e.clientX - r.left) / r.width) * 100 + "%");
    t.style.setProperty("--my", ((e.clientY - r.top) / r.height) * 100 + "%");
  }
}, { passive: true });

/* Wallet fan */
function renderWallet() {
  const fan = $("#fan");
  const ids = state.cards.slice(0, 6);
  if (!ids.length) {
    fan.innerHTML = `<button class="empty" type="button" id="add-first"><span><b style="font-size:32px;display:block">＋</b>Add the cards you carry.<br>Just pick names from a list.</span></button>`;
    $("#add-first").onclick = openWallet;
    return;
  }
  const n = ids.length;
  fan.innerHTML = ids.map((id, i) => {
    const k = i - (n - 1) / 2;
    const rest = `translate3d(${k * 14}px, ${Math.abs(k) * 8 - i * 4}px, ${i * 10}px) rotate(${k * 7}deg)`;
    const spread = `translate3d(${k * 54}px, ${Math.abs(k) * 18 - 10}px, ${i * 10}px) rotate(${k * 12}deg)`;
    return cardEl(cardById(id), false, `transform:${rest};--spread:${spread};z-index:${i}`);
  }).join("");
}
if (!calm) {
  const fan = $("#fan");
  fan.addEventListener("pointermove", (e) => {
    const r = fan.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
    fan.style.transform = `rotateY(${x * 22}deg) rotateX(${-y * 18}deg)`;
  });
  fan.addEventListener("pointerleave", () => (fan.style.transform = ""));
}

function renderCardList(filter = "") {
  const f = filter.trim().toLowerCase();
  $("#card-list").innerHTML = allCards
    .filter((c) => !f || c.name.toLowerCase().includes(f))
    .map((c) => `<label class="card-opt">
      <input type="checkbox" value="${c.id}" ${state.cards.includes(c.id) ? "checked" : ""}>
      ${cardEl(c, true)}<span>${esc(c.name)}</span>
      ${c.rotating ? `<span class="rot"><input type="checkbox" data-rot="${c.id}" ${state.rotating.includes(c.id) ? "checked" : ""}> 5% on</span>` : ""}
    </label>`).join("");
}
function renderBonusSelect() {
  const sel = $("#bonus-card");
  sel.innerHTML = `<option value="">No</option>` + state.cards.map((id) => `<option value="${id}">${esc(cardById(id)?.name)}</option>`).join("");
  sel.value = state.bonus?.card_id && state.cards.includes(state.bonus.card_id) ? state.bonus.card_id : "";
  $("#bonus-left").value = state.bonus?.remaining ?? "";
  $("#bonus-left-wrap").hidden = !sel.value;
}
function openWallet() {
  renderCardList();
  renderBonusSelect();
  $("#portal").checked = state.portal;
  $$("[data-member]").forEach((b) => (b.checked = state.members.includes(b.dataset.member)));
  $("#card-filter").value = "";
  $("#wallet-dialog").showModal();
}
function walletChanged() {
  save();
  renderWallet();
  renderWishCount();
  renderTicker();
  if (current) openDeal(current);
  if (!$("#tab-guide").hidden) renderGuide();
}

$("#edit-wallet").onclick = openWallet;
$("#card-filter").oninput = (e) => renderCardList(e.target.value);
$("#card-list").addEventListener("change", (e) => {
  const t = e.target;
  if (t.dataset.rot) {
    state.rotating = t.checked ? [...new Set([...state.rotating, t.dataset.rot])] : state.rotating.filter((x) => x !== t.dataset.rot);
  } else {
    state.cards = t.checked ? [...new Set([...state.cards, t.value])] : state.cards.filter((x) => x !== t.value);
    renderBonusSelect();
  }
  save();
});
$("#portal").onchange = (e) => { state.portal = e.target.checked; save(); };
$$("[data-member]").forEach((b) => (b.onchange = () => {
  state.members = $$("[data-member]").filter((x) => x.checked).map((x) => x.dataset.member);
  save();
}));
$("#bonus-card").onchange = (e) => {
  state.bonus = e.target.value ? { card_id: e.target.value, remaining: Number($("#bonus-left").value) || 0 } : null;
  $("#bonus-left-wrap").hidden = !e.target.value;
  save();
};
$("#bonus-left").oninput = (e) => { if (state.bonus) { state.bonus.remaining = Number(e.target.value) || 0; save(); } };
$("#wallet-dialog").addEventListener("close", walletChanged);

/* Headline word and ticker */
const WORDS = ["headphones", "sneakers", "laptops", "air fryers", "TVs", "strollers", "everything"];
let wi = 0;
if (!calm) {
  setInterval(() => {
    const el = $("#rotor-word");
    el.className = "out";
    setTimeout(() => { wi = (wi + 1) % WORDS.length; el.textContent = WORDS[wi]; el.className = "in"; }, 340);
  }, 2400);
}

async function renderTicker() {
  let items = [
    "Costco takes <b>Visa</b>, not Amex", "Target matches <b>Amazon</b> prices", "Amazon + Prime Visa = <b>5%</b>",
    "Warranty perks beat <b>protection plans</b>", "Superstores aren't <b>groceries</b>", "Fake reviews get <b>discounted</b>",
  ];
  if (state.cards.length) {
    try {
      const d = await api("/api/wallet", { cards: state.cards, rotating: state.rotating });
      items = d.guide.slice(0, 8).map((g) => `${esc(g.category)} → <b>${esc(g.card)} ${fmtRate(g.rate)}</b>`);
    } catch {}
  }
  const row = items.map((t) => `<span>${t}</span>`).join("");
  $("#ticker").innerHTML = row + row;
}
const fmtRate = (r) => (r % 1 ? r.toFixed(1) : r) + "%";

/* Inbox deals (Gmail) */
let inbox = { connected: false, sample: false, available: false, coupons: [] };

function offerText(c) {
  const what = c.amount ? `$${c.amount} off` : `${c.percent}% off`;
  return c.min_spend ? `${what} $${c.min_spend}+` : what;
}

function renderInbox() {
  const a = $("#inbox-actions");
  if (inbox.connected) {
    a.innerHTML = `<form method="post" action="/auth/google/logout"><button class="pill">Disconnect Gmail</button></form>`;
    $("#inbox-sub").textContent = `Read-only. Found ${inbox.coupons.length} offer${inbox.coupons.length === 1 ? "" : "s"} in your last 30 days of promotions. Nothing is saved.`;
  } else {
    a.innerHTML = `${inbox.available ? `<a class="gbtn" href="/auth/google"><span class="g">G</span> Connect Gmail</a>` : ""}
      <button class="pill" id="inbox-demo">${inbox.sample ? "Hide sample" : "Try with sample inbox"}</button>`;
    $("#inbox-demo").onclick = async () => {
      if (inbox.sample) inbox = { ...inbox, sample: false, coupons: [] };
      else inbox = await api("/api/coupons?demo=true");
      renderInbox();
      if (current) openDeal(current);
    };
    if (!inbox.available) $("#inbox-sub").textContent = "Gmail sign-in isn't set up on this server yet (it needs a Google OAuth client). Try the sample inbox to see how it works.";
  }
  $("#coupons").innerHTML = inbox.coupons.map((c, i) => `
    <div class="coupon ${c.store_key ? "" : "elsewhere"}" style="--i:${i}">
      <div class="c-top"><span class="c-store">${esc(c.store)}</span>${c.personal ? `<span class="tag win">Just for you</span>` : ""}${inbox.sample ? `<span class="tag">Sample</span>` : ""}</div>
      <div class="c-off">${esc(offerText(c))}</div>
      <div class="c-sub">${esc(c.subject)}</div>
      <div class="c-bot">
        ${c.code ? `<button class="c-code" data-code="${esc(c.code)}" title="Copy code">${esc(c.code)} ⧉</button>` : `<span class="c-auto">No code needed</span>`}
        ${c.expires ? `<span class="c-exp">ends ${esc(new Date(c.expires + "T12:00").toLocaleDateString("en-US", { month: "short", day: "numeric" }))}</span>` : ""}
      </div>
      ${c.store_key ? "" : `<div class="c-note">Not a store ShopWiz compares yet</div>`}
    </div>`).join("");
}
$("#coupons").addEventListener("click", (e) => copyCode(e.target.closest("[data-code]")));
function copyCode(b) {
  if (!b) return;
  navigator.clipboard?.writeText(b.dataset.code).then(() => {
    const t = b.textContent; b.textContent = "Copied ✓"; setTimeout(() => (b.textContent = t), 1200);
  }).catch(() => {});
}

/* Search */
let products = [];
let current = null;

async function search(q) {
  $("#results").hidden = false;
  $("#deal-wrap").hidden = true;
  current = null;
  $("#products").innerHTML = `<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>`;
  $("#results").scrollIntoView({ behavior: calm ? "auto" : "smooth", block: "start" });
  try {
    const data = await api("/api/search?q=" + encodeURIComponent(q));
    products = data.products;
    const i = data.intent;
    const bits = [`<b>${esc(i.query)}</b>`];
    if (i.max_price) bits.push(`under <b>${money(i.max_price)}</b>`);
    if (i.min_rating) bits.push(`<b>${i.min_rating}★</b>+`);
    if (i.use) bits.push(`for <b>${esc(i.use)}</b>`);
    $("#intent").innerHTML = "› searching " + bits.join(" · ") + (data.sample ? " · <i>sample mode has 3 headphones</i>" : "");
    $("#intent").hidden = false;
    renderProducts();
  } catch (err) {
    $("#products").innerHTML = `<p class="status error">${esc(err.message)}</p>`;
  }
}

function renderProducts() {
  if (!products.length) {
    $("#products").innerHTML = `<p class="status">Nothing matched. Try fewer words or a higher price limit.</p>`;
    return;
  }
  $("#products").innerHTML = products.map((p, i) => `
    <button class="product" type="button" data-i="${i}" aria-pressed="false" style="--i:${i}">
      ${p.image ? `<img class="thumb" src="${esc(safeUrl(p.image))}" alt="" loading="lazy">` : `<span class="thumb">${esc((p.title || "?")[0])}</span>`}
      <span><span class="t">${esc(p.title)}</span>
        <span class="price">${esc(p.price)}</span>
        <span class="m">${p.merchant ? esc(p.merchant) + " · " : ""}${p.rating ? `${p.rating}★ (${Number(p.reviews || 0).toLocaleString()})` : "No ratings yet"}</span></span>
    </button>`).join("");
}

$("#products").addEventListener("click", (e) => {
  const b = e.target.closest(".product");
  if (!b) return;
  $$(".product").forEach((x) => x.setAttribute("aria-pressed", x === b));
  openDeal(products[Number(b.dataset.i)]);
});
$("#search").onsubmit = (e) => {
  e.preventDefault();
  const q = $("#q").value.trim();
  if (q) search(q);
};
$("#examples").onclick = (e) => {
  if (e.target.tagName !== "BUTTON") return;
  $("#q").value = e.target.textContent;
  search(e.target.textContent);
};

/* Deal */
async function openDeal(product) {
  current = product;
  $("#deal-wrap").hidden = false;
  const el = $("#deal");
  if (!state.cards.length) {
    el.innerHTML = `<div class="panel"><h3>Add your cards first</h3><p>So we can tell you which one to swipe at each store.</p><button class="pill" id="deal-add">＋ Add cards</button></div>`;
    $("#deal-add").onclick = openWallet;
    return;
  }
  el.innerHTML = `<div class="skeleton" style="height:420px"></div><div class="skeleton" style="height:420px"></div>`;
  $("#deal-wrap").scrollIntoView({ behavior: calm ? "auto" : "smooth", block: "start" });
  try {
    const d = await api("/api/deal", {
      catalog_id: product.catalog_id || "",
      product_id: product.product_id || "",
      rating: product.rating || null,
      reviews: product.reviews || null,
      cards: state.cards,
      rotating: state.rotating,
      use_portal: state.portal,
      members: state.members,
      coupons: inbox.coupons.filter((c) => c.store_key),
      title: product.title || "",
      bonus: state.bonus,
      review_url: /amazon\./.test(product.link || "") ? product.link : null,
    });
    if (current !== product) return;
    renderDeal(product, d);
  } catch (err) {
    el.innerHTML = `<p class="status error">${esc(err.message)}</p>`;
  }
}

function countDown(el, from, to, ms = 1100) {
  if (calm) { el.textContent = money(to); return; }
  const t0 = performance.now();
  const step = (t) => {
    const k = Math.min(1, (t - t0) / ms), e = 1 - Math.pow(1 - k, 3);
    el.textContent = money(from + (to - from) * e);
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function burst(el) {
  if (calm) return;
  const r = el.getBoundingClientRect();
  const colors = ["#d4ff3a", "#ff3d8b", "#4d7cff", "#ffffff"];
  for (let i = 0; i < 36; i++) {
    const s = document.createElement("i");
    s.className = "spark";
    s.style.cssText = `left:${r.left + r.width / 2}px;top:${r.top + 30}px;background:${colors[i % 4]}`;
    document.body.appendChild(s);
    const a = Math.random() * Math.PI * 2, v = 80 + Math.random() * 160;
    s.animate([
      { transform: "translate(0,0) rotate(0)", opacity: 1 },
      { transform: `translate(${Math.cos(a) * v}px, ${Math.sin(a) * v + 120}px) rotate(${Math.random() * 720}deg)`, opacity: 0 },
    ], { duration: 900 + Math.random() * 500, easing: "cubic-bezier(.2,.8,.3,1)" }).onfinish = () => s.remove();
  }
}

function renderDeal(product, d) {
  const b = d.best;
  const card = cardById(b.card_id);
  const sticker = Math.max(...d.offers.map((o) => o.price));
  const saved = d.offers.length > 1 ? d.saved_vs_sticker : 0;

  const left = `
    <div class="left">
      <div class="terminal" id="terminal">
        <div class="screen">
          <div class="st">${esc(b.merchant.toUpperCase())} · TOTAL</div>
          <div class="big" id="pay">${money(b.price)}</div>
          <span class="ok">APPROVED · ${esc(b.why || "")}</span>
        </div>
        <div class="slot">${card ? cardEl(card) : ""}</div>
        <div class="keys">${"<i></i>".repeat(9)}</div>
      </div>
      <article class="receipt" aria-label="Best way to buy">
        <p class="item">${esc(product.title)}</p>
        <div class="row"><span>STORE</span><span>${esc(b.merchant)}</span></div>
        <div class="row"><span>PRICE</span><span>${money(b.price)}</span></div>
        ${b.card ? `<div class="row"><span>${esc(card?.name || b.card)}</span><span>−${money(b.back)}</span></div>` : ""}
        ${b.coupon ? `<div class="row"><span>${b.coupon.code ? `CODE ${esc(b.coupon.code)}` : "INBOX OFFER"}</span><span>−${money(b.coupon_off)}</span></div>` : ""}
        ${d.portal.on && b.portal_back ? `<div class="row"><span>${esc(d.portal.name)} (est.)</span><span>−${money(b.portal_back)}</span></div>` : ""}
        <div class="rule"></div>
        <div class="row total"><span>YOU PAY</span><span>${money(b.net_price)}</span></div>
        ${b.member_note ? `<p class="perk">! ${esc(b.member_note)}</p>` : ""}
        ${b.note ? `<p class="perk">* ${esc(b.note)}</p>` : ""}
        ${b.return_note ? `<p class="perk">↩ ${esc(b.return_note)}</p>` : ""}
        ${b.perks.map((p) => `<p class="perk">✓ ${esc(p)}</p>`).join("")}
        ${b.coupon ? `<p class="perk">🏷 From your inbox: ${esc(b.coupon.subject || offerText(b.coupon))}${b.coupon.code ? ` <button class="r-copy" data-code="${esc(b.coupon.code)}">copy code</button>` : ""}</p>` : ""}
        <div class="barcode"></div>
      </article>
      <a class="go" href="${esc(safeUrl(b.link))}" target="_blank" rel="noopener">Go to ${esc(b.merchant)} ↗</a>
      <button class="pill save-wish" id="save-wish" type="button" aria-pressed="${isSaved(product)}">${isSaved(product) ? "♥ On your wishlist" : "♡ Save to wishlist"}</button>
    </div>`;

  const stats = `
    <div class="stats">
      <div class="stat hot"><b>${money(saved)}</b><span>less than the priciest store</span></div>
      <div class="stat"><b>${money(b.back + (d.portal.on ? b.portal_back : 0) + (b.coupon_off || 0))}</b><span>${b.coupon_off ? "off with coupon + rewards" : "back in rewards"}</span></div>
      <div class="stat"><b>${d.offers.length}</b><span>stores compared</span></div>
    </div>`;

  const tip = d.price_match ? `
    <div class="panel tip" id="tip" style="--i:1">
      <span class="label">⚡ Pro move · save ${money(d.price_match.saves)} more</span>
      <p>Buy at <b>${esc(d.price_match.store)}</b>, ask them to price match ${esc(d.price_match.match_to)} at <b>${money(d.price_match.matched_price)}</b>, and pay with <b>${esc(d.price_match.card)}</b> (${esc(d.price_match.why)}). You pay <b>${money(d.price_match.net_price)}</b>.</p>
      <p><small>Stores match identical new items sold by the competitor itself. Show the listing at checkout.</small></p>
    </div>` : "";

  const bonus = d.bonus ? `<div class="panel bonus" style="--i:2"><span class="label">Sign-up bonus</span><p>${esc(d.bonus.text)}</p></div>` : "";

  const r = d.rating;
  const trust = `
    <div class="panel" style="--i:3">
      <h3>Can you trust the rating?</h3>
      ${r.rating ? `<div class="trust">
        <div class="gauge" id="gauge"><span>${r.rating.toFixed(1)}</span><small>TRUST</small></div>
        <div>
          <div class="sub">Stores say <b>${r.raw_rating.toFixed(1)}★</b> from ${r.total_reviews.toLocaleString()} ratings at ${r.sources} store${r.sources === 1 ? "" : "s"}.
          ${r.flagged_share != null ? `<b>${Math.round(r.flagged_share * 100)}%</b> of the reviews we read look suspicious.` : "Adjusted for how many ratings there are."}</div>
          ${r.flags.length ? `<ul class="flags">${r.flags.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : ""}
        </div>
      </div>` : `<p class="sub">No store ratings found for this product yet.</p>`}
    </div>`;

  const lo = Math.min(...d.offers.map((o) => o.net_price));
  const race = `
    <div class="panel" style="--i:4">
      <h3>The price race, after your best card</h3>
      <ul class="race">${d.offers.map((o) => {
        const win = o.merchant === b.merchant && o.price === b.price;
        const w = 100 - ((o.net_price - lo) / Math.max(1, sticker - lo)) * 55;
        return `<li class="${win ? "win" : ""}">
          <span class="store">${esc(o.merchant)} ${win ? `<span class="tag win">Best</span>` : ""}${o.condition !== "new" ? `<span class="tag warn">${esc(o.condition)}</span>` : ""}${o.needs_membership ? `<span class="tag">Members</span>` : ""}${o.return_days && o.return_days === Math.max(...d.offers.map((x) => x.return_days || 0)) ? `<span class="tag">Longest returns</span>` : ""}</span>
          <span class="net">${money(o.net_price)}${o.net_price < o.price ? `<s>${money(o.price)}</s>` : ""}</span>
          <span class="bar"><i data-w="${w}"></i></span>
          <span class="how">${o.card_id ? cardEl(cardById(o.card_id), true) : ""}<span>${o.card ? `${esc(o.card)} · ${esc(o.why)}` : esc(o.why)}${o.coupon ? ` · <b class="hi-t">🏷 ${o.coupon.code ? esc(o.coupon.code) + " " : ""}−${money(o.coupon_off)}</b>` : ""}${o.return_note ? ` · ↩ ${esc(o.return_note)}` : ""}${o.member_note ? ` · <b class="warn-t">${esc(o.member_note)}</b>` : ""}</span></span>
        </li>`;
      }).join("")}</ul>
    </div>`;

  const owners = d.aspects.length || d.verdict ? `
    <div class="panel" style="--i:3">
      <h3>What owners say</h3>
      ${d.verdict ? `<div class="verdict"><p><span class="label">Great for</span> ${esc(d.verdict.best_for)}</p>${d.verdict.skip_if ? `<p><span class="label skip">Skip if</span> ${esc(d.verdict.skip_if)}</p>` : ""}</div>` : ""}
      <ul class="aspects">${d.aspects.map((a) => `
        <li title="${esc(a.quote)}">
          <span class="an">${esc(a.aspect)}</span>
          <span class="ap ${a.positive >= 0.67 ? "good" : a.positive < 0.5 ? "bad" : ""}">${Math.round(a.positive * 100)}% happy</span>
          <span class="meter"><i data-w="${a.positive * 100}"></i></span>
          <q>${esc(a.quote)}</q>
        </li>`).join("")}</ul>
      <p class="fine">From ${d.sample ? "sample" : "recent Amazon"} reviews, leaving out the ones that look fake.</p>
    </div>` : "";

  $("#deal").onclick = (e) => {
    copyCode(e.target.closest("[data-code]"));
    if (e.target.closest("#save-wish")) toggleWish(product, b.net_price);
  };
  $("#deal").innerHTML = `${left}<div class="side">${stats}${tip}${bonus}${trust}${owners}${race}</div>`;

  // Choreography: swipe, approve, count down to the real price, fill the bars and gauge.
  setTimeout(() => {
    $("#terminal")?.classList.add("done");
    const pay = $("#pay");
    if (pay) countDown(pay, b.price, b.net_price);
  }, calm ? 0 : 1100);
  requestAnimationFrame(() => setTimeout(() => {
    $$(".race .bar i, .aspects .meter i").forEach((i) => (i.style.width = i.dataset.w + "%"));
    const g = $("#gauge");
    if (g) g.style.setProperty("--p", (r.rating / 5) * 100);
  }, calm ? 0 : 500));
  if (d.price_match) setTimeout(() => burst($("#tip")), calm ? 0 : 1300);
}

/* Cheat sheet */
async function renderGuide() {
  const el = $("#guide");
  if (!state.cards.length) {
    el.innerHTML = `<div class="panel" style="grid-column:1/-1"><h3>Add your cards to see your cheat sheet</h3><button class="pill" id="guide-add">＋ Add cards</button></div>`;
    $("#guide-add").onclick = openWallet;
    return;
  }
  el.innerHTML = `<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>`;
  try {
    const d = await api("/api/wallet", { cards: state.cards, rotating: state.rotating });
    el.innerHTML = d.guide.map((g, i) => `
      <div class="gtile" style="--i:${i}">
        <span class="cat">${esc(g.category)}</span>
        <span class="rate">${(g.rate % 1 ? g.rate.toFixed(1) : g.rate)}<small>%</small></span>
        <span class="who">${cardEl(cardById(g.card_id), true)}${esc(g.card)}</span>
        ${g.note ? `<span class="alt">${esc(g.note)}</span>` : ""}
        ${g.runner_up ? `<span class="alt">Backup: ${esc(g.runner_up)} (${fmtRate(g.runner_up_rate)})</span>` : ""}
      </div>`).join("");
  } catch (err) {
    el.innerHTML = `<p class="status error">${esc(err.message)}</p>`;
  }
}


/* Wishlist: judge each saved item as buy now, wait or skip */
const wishId = (p) => String(p.catalog_id || p.product_id || p.title || "").slice(0, 60);
const isSaved = (p) => state.wishlist.some((w) => w.id === wishId(p));
const today = () => new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD in the shopper's own time zone
const fmtDay = (iso) => new Date(iso + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" });
const busy = new Set();
let wishPlan = null;
const MAX_WISH = 12;

function toggleWish(p, netPrice) {
  const id = wishId(p);
  if (isSaved(p)) {
    state.wishlist = state.wishlist.filter((w) => w.id !== id);
  } else if (state.wishlist.length >= MAX_WISH) {
    alert(`Your wishlist holds ${MAX_WISH} items. Remove one to add another.`);
    return;
  } else {
    state.wishlist.push({
      id, title: p.title || "", catalog_id: p.catalog_id || "", product_id: p.product_id || "", image: p.image || "",
      link: p.link || "", merchant: p.merchant || "", rating: p.rating || null, reviews: p.reviews || null,
      list_price: p.original_price_value || null, added: today(), added_price: netPrice,
      history: [{ d: today(), p: netPrice }], need_by: null, last: null, lastAt: null,
    });
  }
  save();
  const b = $("#save-wish");
  if (b) { b.setAttribute("aria-pressed", isSaved(p)); b.textContent = isSaved(p) ? "♥ On your wishlist" : "♡ Save to wishlist"; }
  renderWishCount();
}

function renderWishCount() {
  const n = state.wishlist.length, el = $("#wish-count");
  el.hidden = !n;
  el.textContent = n;
}

function spark(h) {
  if (h.length < 2) return "";
  const ps = h.map((x) => x.p), lo = Math.min(...ps), hi = Math.max(...ps), span = hi - lo || 1;
  const pts = h.map((x, i) => [(i / (h.length - 1)) * 116 + 2, 28 - ((x.p - lo) / span) * 24]);
  const [lx, ly] = pts[pts.length - 1];
  return `<svg class="trend" viewBox="0 0 120 32" role="img" aria-label="Price history from ${money(hi)} to ${money(ps[ps.length - 1])}">
    <polyline points="${pts.map((p) => p.join(",")).join(" ")}" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${lx}" cy="${ly}" r="3" fill="currentColor"/></svg>`;
}

const VERDICT = { buy_now: "Buy now", wait: "Wait", skip: "Skip" };
function ago(ms) {
  const m = Math.round((Date.now() - ms) / 60000);
  return m < 2 ? "just now" : m < 90 ? `${m} min ago` : m < 2880 ? `${Math.round(m / 60)} hours ago` : `${Math.round(m / 1440)} days ago`;
}

function wishCard(it, i) {
  const r = it.last, t = r?.timing, working = busy.has(it.id);
  const stamp = working ? `<span class="stamp idle">Checking…</span>`
    : t ? `<span class="stamp ${t.verdict}">${VERDICT[t.verdict]}</span>` : `<span class="stamp idle">Not checked</span>`;
  const drop = r && it.added_price ? r.best.net_price - it.added_price : 0;
  const price = r ? `<span class="wprice">${money(r.best.net_price)}</span>
      <span class="wstore">${esc(r.best.merchant)}${r.best.card ? ` · pay with ${esc(r.best.card)}` : ""}</span>`
    : `<span class="wstore">Saved at ${money(it.added_price)}</span>`;
  const moved = r && Math.abs(drop) >= 1 ? `<span class="tag ${drop < 0 ? "win" : "warn"}">${drop < 0 ? "−" : "+"}${money(Math.abs(drop))} since saved</span>` : "";
  return `<article class="wish ${t ? t.verdict : ""}" data-id="${esc(it.id)}" style="--i:${i}">
    ${it.image ? `<img class="thumb" src="${esc(safeUrl(it.image))}" alt="" loading="lazy">` : `<span class="thumb">${esc((it.title || "?")[0])}</span>`}
    <div class="wbody">
      <div class="whead">${stamp}<h3 class="wtitle">${esc(it.title)}</h3></div>
      <div class="wline">${price}${moved}</div>
      ${t ? `<p class="wheadline">${esc(t.headline)}</p>
        <ul class="wreasons">${t.reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : `<p class="fine">Press “Check the market” to get a verdict.</p>`}
      <div class="wfoot">
        ${t ? `<span class="tag">${esc(t.confidence)} confidence</span>` : ""}
        ${it.lastAt ? `<span class="fine">Checked ${ago(it.lastAt)}</span>` : ""}
        <label class="needby">Need it by <input type="date" data-act="needby" value="${esc(it.need_by || "")}" min="${today()}"></label>
      </div>
    </div>
    <div class="wside">
      ${spark(it.history)}
      ${it.history.length > 1 ? `<span class="fine">${it.history.length} checks</span>` : `<span class="fine">History starts now</span>`}
      <button class="pill" data-act="deal" type="button">Full deal</button>
      <button class="pill" data-act="check" type="button">Check</button>
      <button class="pill quiet" data-act="remove" type="button" aria-label="Remove ${esc(it.title)} from wishlist">Remove</button>
    </div>
  </article>`;
}

function renderWish() {
  renderWishCount();
  const list = $("#wish-list"), items = state.wishlist;
  $("#wish-budget").value = state.budget || "";
  if (!items.length) {
    list.innerHTML = `<div class="panel"><h3>Nothing saved yet</h3><p class="sub">Search for something, open its deal, and press “♡ Save to wishlist”. ShopWiz will keep an eye on it for you.</p><button class="pill" id="wish-go" type="button">Find something</button></div>`;
    $("#wish-go").onclick = () => $$(".tabs button")[0].click();
    $("#wish-summary").innerHTML = "";
    return;
  }
  const order = wishPlan ? wishPlan.order : [];
  const sorted = [...items].sort((a, b) => {
    const ia = order.indexOf(a.id), ib = order.indexOf(b.id);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });
  list.innerHTML = sorted.map(wishCard).join("");
  renderSummary();
}

function renderSummary() {
  const el = $("#wish-summary"), p = wishPlan;
  if (!p || !state.wishlist.some((w) => w.last)) { el.innerHTML = ""; return; }
  const byId = (id) => state.wishlist.find((w) => w.id === id);
  const cart = p.cart.map(byId).filter(Boolean);
  el.innerHTML = `
    <div class="stats">
      <div class="stat hot"><b>${p.buy_now.length}</b><span>worth buying today${p.cart_saves ? ` · saves ${money(p.cart_saves)} vs list` : ""}</span></div>
      <div class="stat"><b>${p.wait.length}</b><span>worth waiting for${p.wait_savings ? ` · could save ${money(p.wait_savings)} more` : ""}</span></div>
      <div class="stat"><b>${p.skip.length}</b><span>to skip</span></div>
    </div>
    ${cart.length ? `<div class="panel cart"><h3>Buy these today${state.budget ? ` (budget ${money(state.budget)})` : ""}</h3>
      <ul>${cart.map((w) => `<li><span>${esc(w.title)}</span><b>${money(w.last.best.net_price)}</b></li>`).join("")}</ul>
      <div class="row total"><span>Total</span><b>${money(p.cart_total)}</b></div>
      ${p.left_over != null ? `<p class="fine">${money(p.left_over)} left of your budget.</p>` : ""}</div>` : ""}`;
}

async function refreshPlan() {
  const done = state.wishlist.filter((w) => w.last);
  if (!done.length) { wishPlan = null; return; }
  try {
    wishPlan = await api("/api/wishlist/plan", {
      budget: state.budget || null,
      items: done.map((w) => ({
        id: w.id, verdict: w.last.timing.verdict, score: w.last.timing.score, net_price: w.last.best.net_price,
        saves: w.last.saves || 0, target_price: w.last.timing.target_price, wait_until: w.last.timing.wait_until,
      })),
    });
  } catch {}
}

async function checkItem(it) {
  busy.add(it.id);
  renderWish();
  try {
    const r = await api("/api/wishlist/check", {
      catalog_id: it.catalog_id, product_id: it.product_id, title: it.title, rating: it.rating, reviews: it.reviews,
      cards: state.cards, rotating: state.rotating, use_portal: state.portal, members: state.members,
      coupons: inbox.coupons.filter((c) => c.store_key), list_price: it.list_price, history: it.history, need_by: it.need_by,
    });
    it.history = it.history.filter((h) => h.d !== today()).concat({ d: today(), p: r.best.net_price }).slice(-30);
    it.last = r;
    it.lastAt = Date.now();
    save();
    return null;
  } catch (err) {
    return err.message;
  } finally {
    busy.delete(it.id);
  }
}

let checking = false;
async function checkAll(only) {
  if (checking) return;
  if (!state.cards.length) { $("#wish-note").textContent = "Add your cards first so prices count your rewards."; openWallet(); return; }
  checking = true;
  $("#wish-check").disabled = true;
  $("#wish-note").textContent = "Checking stores…";
  const queue = [...(only ? [only] : state.wishlist)], errors = [];
  const worker = async () => {
    while (queue.length) {
      const msg = await checkItem(queue.shift());
      if (msg) errors.push(msg);
      renderWish();
    }
  };
  await Promise.all([worker(), worker()]); // two at a time keeps the bill and the wait down
  await refreshPlan();
  checking = false;
  $("#wish-check").disabled = false;
  $("#wish-note").textContent = errors.length ? errors[0] : "Done.";
  renderWish();
}

async function renderMarket() {
  try {
    const m = await api("/api/market");
    $("#market").innerHTML = m.events.map((e, i) => `
      <div class="mchip${i === 0 ? " next" : ""}"><b>${e.days}</b><span>${e.days === 1 ? "day" : "days"} to<br>${esc(e.name)}${e.approx ? " (about)" : ""}<br><small>${fmtDay(e.date)}</small></span></div>`).join("");
  } catch {
    $("#market").innerHTML = "";
  }
}

$("#wish-check").onclick = () => checkAll();
$("#wish-budget").onchange = async (e) => {
  state.budget = Math.max(0, Number(e.target.value) || 0) || null;
  save();
  await refreshPlan();
  renderWish();
};
$("#wish-list").addEventListener("click", (e) => {
  const act = e.target.closest("[data-act]")?.dataset.act, card = e.target.closest(".wish");
  if (!act || !card) return;
  const it = state.wishlist.find((w) => w.id === card.dataset.id);
  if (!it) return;
  if (act === "remove") {
    state.wishlist = state.wishlist.filter((w) => w !== it);
    save();
    refreshPlan().then(renderWish);
    renderWish();
  } else if (act === "check") {
    checkAll(it);
  } else if (act === "deal") {
    $$(".tabs button")[0].click();
    openDeal(it);
  }
});
$("#wish-list").addEventListener("change", (e) => {
  if (e.target.dataset.act !== "needby") return;
  const it = state.wishlist.find((w) => w.id === e.target.closest(".wish").dataset.id);
  if (!it) return;
  it.need_by = e.target.value || null;
  save();
  checkAll(it); // the offers are cached for an hour, so this is usually free
});

/* Looks */
function markSkin() {
  $$(".skins button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.skin === document.documentElement.dataset.skin));
}
$$(".skins button").forEach((b) => (b.onclick = () => {
  document.documentElement.dataset.skin = b.dataset.skin;
  try { localStorage.setItem("shopwiz.skin", b.dataset.skin); } catch {}
  markSkin();
}));
markSkin();

/* Tabs */
$$(".tabs button").forEach((btn) => {
  btn.onclick = () => {
    $$(".tabs button").forEach((b) => b.setAttribute("aria-selected", b === btn));
    $("#tab-shop").hidden = btn.dataset.tab !== "shop";
    $("#tab-wish").hidden = btn.dataset.tab !== "wish";
    $("#tab-guide").hidden = btn.dataset.tab !== "guide";
    if (btn.dataset.tab === "guide") renderGuide();
    if (btn.dataset.tab === "wish") { renderMarket(); refreshPlan().then(renderWish); renderWish(); }
  };
});

/* Start */
(async function start() {
  try {
    const [config, cards] = await Promise.all([api("/api/config"), api("/api/cards")]);
    allCards = cards;
    state.cards = state.cards.filter((id) => cardById(id));
    $("#sample-banner").hidden = config.live;
    $("#as-of").textContent = `Card rules as of ${config.cards_as_of}`;
  } catch {
    $("#sample-banner").hidden = false;
    $("#sample-banner").textContent = "Can't reach the ShopWiz server right now.";
  }
  renderWallet();
  renderTicker();
  try { inbox = await api("/api/coupons"); } catch {}
  renderInbox();
  if (location.hash === "#inbox") $("#inbox").scrollIntoView();
  if (location.hash === "#inbox-error") $("#inbox-sub").textContent = "Gmail sign-in didn't finish. Try again.";
})();
