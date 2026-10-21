/*
 * Carte Lovelace « Cantine EDA » — chargée automatiquement par l'intégration.
 *
 *   type: custom:cantine-eda-card
 *   entity: sensor.cantine_xxx_solde
 *   title: Cantine de Léa         # facultatif
 *   show_last_meal: true          # produits du dernier ticket
 *   show_photo: true
 *   show_tickets: true
 *   tickets: 5
 *   show_credentials: true        # boutons identifiant / mot de passe
 *   copy_password_on_recharge: true
 */

const CARD_VERSION = "1.1.0";
const DOMAIN = "cantine_eda";

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

/* Copie dans le presse-papier, y compris quand le texte arrive après un appel
 * réseau (Safari/iOS exigent que la copie démarre pendant le clic). */
async function copyText(textOrPromise) {
  const promise = Promise.resolve(textOrPromise);
  if (navigator.clipboard && window.ClipboardItem && window.isSecureContext) {
    try {
      const item = new ClipboardItem({
        "text/plain": promise.then((t) => new Blob([t], { type: "text/plain" })),
      });
      await navigator.clipboard.write([item]);
      return true;
    } catch (_err) {
      /* on tente les autres méthodes */
    }
  }
  const text = await promise;
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_err) {
      /* repli ci-dessous */
    }
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.cssText = "position:fixed;top:-1000px;opacity:0";
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch (_err) {
    ok = false;
  }
  area.remove();
  return ok;
}

class CantineEdaCard extends HTMLElement {
  static getConfigElement() {
    return document.createElement("cantine-eda-card-editor");
  }

  static getStubConfig(hass) {
    const entity = Object.keys(hass.states).find(
      (id) =>
        id.startsWith("sensor.") &&
        hass.entities?.[id]?.platform === DOMAIN &&
        hass.states[id].attributes.identifiant !== undefined
    );
    return { entity: entity || "", show_photo: true, show_tickets: true, tickets: 5 };
  }

  setConfig(config) {
    if (!config.entity) throw new Error("Choisissez le capteur « Solde » d'un compte cantine");
    this._config = {
      show_last_meal: true,
      show_photo: true,
      show_tickets: true,
      tickets: 5,
      show_credentials: true,
      copy_password_on_recharge: true,
      ...config,
    };
    this._revealed = null;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.addEventListener("click", (ev) => this._onClick(ev));
    }
    this._lastKey = null;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    const attrs = hass.states[this._config?.entity]?.attributes;
    const photoState = attrs?.entite_photo ? hass.states[attrs.entite_photo] : undefined;
    const key = [
      hass.states[this._config?.entity]?.last_updated,
      photoState?.attributes?.entity_picture,
      hass.locale?.language,
      hass.themes?.darkMode,
    ].join("|");
    if (key !== this._lastKey) {
      this._lastKey = key;
      this._render();
    }
  }

  getCardSize() {
    return 3 + (this._config?.show_photo ? 4 : 0) + (this._config?.show_tickets ? Math.ceil(this._config.tickets / 2) : 0);
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  /* ----------------------------------------------------------- formatage */
  get _lang() {
    return this._hass?.locale?.language || this._hass?.language || "fr";
  }

  _money(value) {
    if (value === null || value === undefined || value === "" || isNaN(Number(value))) return "—";
    return new Intl.NumberFormat(this._lang, { style: "currency", currency: "EUR" }).format(Number(value));
  }

  _date(iso, opts) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return "";
    return new Intl.DateTimeFormat(this._lang, opts).format(d);
  }

  _relative(iso) {
    if (!iso) return "jamais";
    const d = new Date(iso);
    if (isNaN(d)) return "";
    const startOf = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const days = Math.round((startOf(d) - startOf(new Date())) / 86400000);
    const time = this._date(iso, { hour: "2-digit", minute: "2-digit" });
    if (Math.abs(days) <= 6) {
      const rtf = new Intl.RelativeTimeFormat(this._lang, { numeric: "auto" });
      return `${rtf.format(days, "day")} · ${time}`;
    }
    return this._date(iso, { weekday: "short", day: "numeric", month: "short" });
  }

  /* -------------------------------------------------------------- rendu */
  _render() {
    if (!this.shadowRoot || !this._config) return;
    const hass = this._hass;
    const stateObj = hass?.states[this._config.entity];
    if (!hass) return;
    if (!stateObj) {
      this.shadowRoot.innerHTML = `${STYLE}<ha-card><div class="warning">Entité introuvable : ${esc(this._config.entity)}</div></ha-card>`;
      return;
    }
    const a = stateObj.attributes;
    const unavailable = ["unavailable", "unknown"].includes(stateObj.state);
    const balance = Number(stateObj.state);
    const threshold = Number(a.seuil_solde_bas ?? 5);
    const low = !unavailable && balance < threshold;
    const title = this._config.title || `Cantine ${a.identifiant ?? ""}`.trim();

    const tickets = Array.isArray(a.tickets) ? a.tickets : [];
    const now = new Date();
    const monthTickets = tickets.filter((t) => {
      const d = t.date && new Date(t.date);
      return d && d.getMonth() === now.getMonth() && d.getFullYear() === now.getFullYear();
    });
    const monthTotal = monthTickets.reduce((sum, t) => sum + (Number(t.montant) || 0), 0);

    const photoState = a.entite_photo ? hass.states[a.entite_photo] : undefined;
    const photoUrl = photoState && photoState.state !== "unavailable" ? photoState.attributes.entity_picture : null;
    const photoTicket = tickets.find((t) => t.photo) || tickets[0];

    const isAdmin = !!hass.user?.is_admin;
    const showCreds = this._config.show_credentials;
    const canRenew = isAdmin && a.entite_renouvellement && hass.states[a.entite_renouvellement]?.state !== "unavailable";

    const ticketRows = tickets.slice(0, Number(this._config.tickets) || 0).map((t) => {
      const meal = t.repas || (t.lignes || []).map((l) => l.libelle).filter(Boolean).join(", ");
      return `
        <li>
          <div class="t-date">
            <span class="t-day">${esc(this._date(t.date, { day: "numeric" }))}</span>
            <span class="t-month">${esc(this._date(t.date, { weekday: "short" }))}</span>
          </div>
          <div class="t-body">
            <div class="t-label">${esc(t.libelle || "Passage")}${t.photo ? ' <ha-icon class="t-photo" icon="mdi:camera-outline" title="Photo du plateau"></ha-icon>' : ""}</div>
            ${meal ? `<div class="t-lines">${esc(meal)}</div>` : ""}
          </div>
          <div class="t-amount ${Number(t.montant) < 0 ? "refund" : ""}">${t.montant === null || t.montant === undefined ? "" : esc(this._money(-Number(t.montant)))}</div>
        </li>`;
    }).join("");

    const last = tickets[0];
    const lastLines = last && (last.lignes || []).filter((l) => l.libelle);
    const lastMeal = last && lastLines.length ? `
      <div class="meal">
        <div class="meal-head">
          <ha-icon icon="${ICON_BY_ACTIVITY[last.activite] || "mdi:silverware-fork-knife"}"></ha-icon>
          <span class="meal-title">Dernier repas · ${esc(last.libelle || "")}</span>
          <span class="meal-date">${esc(this._date(last.date, { weekday: "short", day: "numeric", month: "short" }))}</span>
        </div>
        <ul>${lastLines.map((l) => `<li><span>${l.quantite && l.quantite !== 1 ? `${esc(l.quantite)} × ` : ""}${esc(l.libelle)}</span></li>`).join("")}</ul>
        ${last.montant !== null && last.montant !== undefined ? `<div class="meal-total">Payé <b>${esc(this._money(last.montant))}</b>${last.prix_complet ? ` · prix avant participations ${esc(this._money(last.prix_complet))}` : ""}</div>` : ""}
      </div>` : "";

    const revealed = this._revealed && this._revealed.until > Date.now() ? this._revealed.password : null;

    this.shadowRoot.innerHTML = `${STYLE}
      <ha-card class="${low ? "low" : ""}">
        <div class="head">
          <div class="avatar"><ha-icon icon="mdi:silverware-fork-knife"></ha-icon></div>
          <div class="who">
            <div class="title">${esc(title)}</div>
            <div class="sub">Dernier passage ${esc(this._relative(a.dernier_passage))}</div>
          </div>
          <button class="icon" data-action="more-info" data-entity="${esc(this._config.entity)}" title="Détails">
            <ha-icon icon="mdi:dots-vertical"></ha-icon>
          </button>
        </div>

        <div class="balance-row">
          <div>
            <div class="label">Solde</div>
            <div class="balance">${unavailable ? "—" : esc(this._money(balance))}</div>
            ${low ? `<div class="pill"><ha-icon icon="mdi:alert-circle"></ha-icon>Solde bas (moins de ${esc(this._money(threshold))})</div>` : ""}
          </div>
          <div class="stats">
            <div><span>${monthTickets.length}</span> passage${monthTickets.length > 1 ? "s" : ""} ce mois</div>
            <div><span>${esc(this._money(monthTotal))}</span> dépensés</div>
          </div>
        </div>

        ${this._config.show_last_meal ? lastMeal : ""}

        ${this._config.show_photo ? `
          <div class="photo ${photoUrl ? "" : "empty"}" ${photoUrl ? `data-action="more-info" data-entity="${esc(a.entite_photo)}"` : ""}>
            ${photoUrl
              ? `<img src="${esc(photoUrl)}" alt="Photo du dernier plateau" loading="lazy">
                 <div class="caption">
                   <span>${esc(photoTicket?.repas || photoTicket?.libelle || "Dernier plateau")}</span>
                   <span>${esc(this._date(photoTicket?.date, { weekday: "long", day: "numeric", month: "long" }))}</span>
                 </div>`
              : `<ha-icon icon="mdi:camera-off-outline"></ha-icon><span>Pas encore de photo de plateau</span>`}
          </div>` : ""}

        ${this._config.show_tickets && ticketRows ? `<ul class="tickets">${ticketRows}</ul>` : ""}

        <div class="actions">
          <button class="primary" data-action="recharge">
            <ha-icon icon="mdi:credit-card-plus-outline"></ha-icon>Recharger
          </button>
          ${showCreds ? `
            <button data-action="copy-user" title="Copier l'identifiant">
              <ha-icon icon="mdi:account-outline"></ha-icon><span class="mono">${esc(a.identifiant)}</span>
            </button>
            <button data-action="copy-password" title="Copier le mot de passe">
              <ha-icon icon="mdi:key-outline"></ha-icon><span class="mono">${revealed ? esc(revealed) : "••••••••"}</span>
            </button>
            <button class="icon" data-action="reveal" title="${revealed ? "Masquer" : "Afficher le mot de passe"}">
              <ha-icon icon="${revealed ? "mdi:eye-off-outline" : "mdi:eye-outline"}"></ha-icon>
            </button>` : ""}
          ${canRenew ? `
            <button class="icon" data-action="renew" title="Renouveler le mot de passe">
              <ha-icon icon="mdi:lock-reset"></ha-icon>
            </button>` : ""}
        </div>
      </ha-card>`;
  }

  /* ------------------------------------------------------------ actions */
  _toast(message) {
    this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message }, bubbles: true, composed: true }));
  }

  _credentials() {
    return this._hass.callWS({ type: `${DOMAIN}/credentials`, entity_id: this._config.entity });
  }

  async _onClick(ev) {
    const target = ev.target.closest("[data-action]");
    if (!target) return;
    const action = target.dataset.action;
    const attrs = this._hass.states[this._config.entity]?.attributes || {};

    if (action === "more-info") {
      this.dispatchEvent(new CustomEvent("hass-more-info", {
        detail: { entityId: target.dataset.entity }, bubbles: true, composed: true,
      }));
      return;
    }

    if (action === "recharge") {
      // Tout se déclenche pendant le clic : copie du mot de passe puis ouverture du site
      let copying = null;
      if (this._config.copy_password_on_recharge && this._config.show_credentials) {
        const creds = this._credentials();
        copying = copyText(creds.then((c) => c.password)).catch(() => false);
      }
      window.open(attrs.url_rechargement, "_blank", "noopener");
      if (copying) {
        this._toast((await copying)
          ? `Mot de passe copié — identifiant : ${attrs.identifiant}`
          : "Site ouvert (mot de passe non copié)");
      }
      return;
    }

    if (action === "copy-user") {
      this._toast((await copyText(attrs.identifiant)) ? "Identifiant copié" : "Copie impossible");
      return;
    }

    if (action === "copy-password") {
      try {
        const ok = await copyText(this._credentials().then((c) => c.password));
        this._toast(ok ? "Mot de passe copié" : "Copie impossible");
      } catch (err) {
        this._toast(err?.message || "Mot de passe non accessible");
      }
      return;
    }

    if (action === "reveal") {
      if (this._revealed && this._revealed.until > Date.now()) {
        this._revealed = null;
        this._render();
        return;
      }
      try {
        const creds = await this._credentials();
        this._revealed = { password: creds.password, until: Date.now() + 20000 };
        this._render();
        clearTimeout(this._hideTimer);
        this._hideTimer = setTimeout(() => { this._revealed = null; this._render(); }, 20000);
      } catch (err) {
        this._toast(err?.message || "Mot de passe non accessible");
      }
      return;
    }

    if (action === "renew") {
      if (!window.confirm("Générer un nouveau mot de passe et le changer sur le site ?\nL'ancien reste récupérable.")) return;
      try {
        await this._hass.callService("button", "press", { entity_id: attrs.entite_renouvellement });
        this._toast("Mot de passe renouvelé");
      } catch (err) {
        this._toast(`Échec : ${err?.message || err}`);
      }
    }
  }
}

const ICON_BY_ACTIVITY = {
  "SELF": "mdi:silverware-fork-knife",
  "BAR&FOYER": "mdi:coffee-outline",
  "RESTAURATION RAPIDE": "mdi:food-outline",
};

const STYLE = `<style>
  :host { --cantine-accent: var(--primary-color); --cantine-low: var(--error-color, #db4437); }
  ha-card { overflow: hidden; padding: 16px; display: flex; flex-direction: column; gap: 14px; }
  ha-card.low { --cantine-accent: var(--cantine-low); }
  .warning { color: var(--error-color); padding: 8px; }
  .head { display: flex; align-items: center; gap: 12px; }
  .avatar { width: 40px; height: 40px; border-radius: 12px; display: grid; place-items: center; flex: none;
    color: var(--cantine-accent); background: color-mix(in srgb, var(--cantine-accent) 14%, transparent); }
  .who { flex: 1; min-width: 0; }
  .title { font-size: 16px; font-weight: 600; color: var(--primary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .sub { font-size: 13px; color: var(--secondary-text-color); }
  .balance-row { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  .label { font-size: 12px; text-transform: uppercase; letter-spacing: .06em; color: var(--secondary-text-color); }
  .balance { font-size: 40px; line-height: 1.1; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--cantine-accent); }
  .pill { display: inline-flex; align-items: center; gap: 4px; margin-top: 6px; padding: 2px 10px 2px 6px; border-radius: 999px;
    font-size: 12px; font-weight: 500; color: var(--cantine-low); background: color-mix(in srgb, var(--cantine-low) 12%, transparent); }
  .pill ha-icon { --mdc-icon-size: 16px; }
  .stats { text-align: right; font-size: 13px; color: var(--secondary-text-color); line-height: 1.6; }
  .stats span { font-weight: 600; color: var(--primary-text-color); font-variant-numeric: tabular-nums; }
  .meal { border-radius: 14px; padding: 12px 14px; background: var(--secondary-background-color); }
  .meal-head { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--secondary-text-color); }
  .meal-head ha-icon { --mdc-icon-size: 18px; color: var(--cantine-accent); }
  .meal-title { flex: 1; font-weight: 600; color: var(--primary-text-color); }
  .meal-date { text-transform: capitalize; }
  .meal ul { margin: 8px 0 0; padding: 0 0 0 26px; font-size: 15px; line-height: 1.5; color: var(--primary-text-color); }
  .meal-total { margin-top: 8px; padding-left: 26px; font-size: 12px; color: var(--secondary-text-color); }
  .meal-total b { color: var(--primary-text-color); font-variant-numeric: tabular-nums; }
  .photo { position: relative; border-radius: 14px; overflow: hidden; aspect-ratio: 4 / 3; cursor: pointer;
    background: var(--secondary-background-color); }
  .photo img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .photo .caption { position: absolute; inset: auto 0 0 0; padding: 28px 12px 10px; display: flex; justify-content: space-between; gap: 8px;
    font-size: 13px; color: #fff; background: linear-gradient(transparent, rgba(0,0,0,.65)); }
  .photo .caption span:first-child { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .photo .caption span:last-child { flex: none; opacity: .9; text-transform: capitalize; }
  .photo.empty { aspect-ratio: auto; padding: 18px; cursor: default; display: flex; align-items: center; justify-content: center; gap: 8px;
    font-size: 13px; color: var(--secondary-text-color); }
  .tickets { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
  .tickets li { display: flex; align-items: center; gap: 12px; padding: 8px 0; border-top: 1px solid var(--divider-color); }
  .tickets li:first-child { border-top: none; }
  .t-date { width: 38px; flex: none; text-align: center; line-height: 1.1; }
  .t-day { display: block; font-size: 17px; font-weight: 600; color: var(--primary-text-color); }
  .t-month { font-size: 11px; text-transform: uppercase; color: var(--secondary-text-color); }
  .t-body { flex: 1; min-width: 0; }
  .t-label { font-size: 14px; color: var(--primary-text-color); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .t-lines { font-size: 12px; color: var(--secondary-text-color); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .t-photo { --mdc-icon-size: 15px; color: var(--secondary-text-color); vertical-align: -2px; }
  .t-amount.refund { color: var(--success-color, #43a047); }
  .t-amount { font-size: 14px; font-variant-numeric: tabular-nums; color: var(--secondary-text-color); }
  .actions { display: flex; flex-wrap: wrap; gap: 8px; }
  button { font: inherit; font-size: 14px; display: inline-flex; align-items: center; gap: 6px; padding: 8px 12px; min-height: 40px;
    border-radius: 10px; border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); cursor: pointer; }
  button:hover { background: var(--secondary-background-color); }
  button ha-icon { --mdc-icon-size: 20px; color: var(--secondary-text-color); }
  button.primary { background: var(--cantine-accent); border-color: transparent; color: var(--text-primary-color, #fff); font-weight: 600; flex: 1 1 140px; justify-content: center; }
  button.primary ha-icon { color: inherit; }
  button.primary:hover { filter: brightness(1.08); }
  button.icon { padding: 8px; border: none; min-width: 40px; justify-content: center; }
  .mono { font-family: var(--code-font-family, ui-monospace, monospace); font-size: 13px; max-width: 12ch; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>`;

class CantineEdaCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._form) this._form.hass = hass;
    else this._render();
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (s) => LABELS[s.name] || s.name;
      this._form.addEventListener("value-changed", (ev) => {
        this.dispatchEvent(new CustomEvent("config-changed", {
          detail: { config: ev.detail.value }, bubbles: true, composed: true,
        }));
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.schema = [
      { name: "entity", required: true, selector: { entity: { filter: { integration: DOMAIN, domain: "sensor", device_class: "monetary" } } } },
      { name: "title", selector: { text: {} } },
      { type: "grid", name: "", schema: [
        { name: "show_last_meal", selector: { boolean: {} } },
        { name: "show_photo", selector: { boolean: {} } },
        { name: "show_tickets", selector: { boolean: {} } },
        { name: "show_credentials", selector: { boolean: {} } },
        { name: "copy_password_on_recharge", selector: { boolean: {} } },
      ] },
      { name: "tickets", selector: { number: { min: 0, max: 15, mode: "slider" } } },
    ];
    this._form.data = {
      show_last_meal: true, show_photo: true, show_tickets: true, tickets: 5, show_credentials: true, copy_password_on_recharge: true,
      ...this._config,
    };
  }
}

const LABELS = {
  entity: "Compte (capteur Solde)",
  title: "Titre",
  show_last_meal: "Détail du dernier repas",
  show_photo: "Photo du dernier plateau",
  show_tickets: "Derniers passages",
  show_credentials: "Boutons identifiant / mot de passe",
  copy_password_on_recharge: "Copier le mot de passe en ouvrant le site",
  tickets: "Nombre de passages affichés",
};

// Le frontend de Home Assistant peut remplacer window.customElements après le
// chargement de ce script : une carte définie trop tôt reste alors introuvable
// (« Custom element doesn't exist » ou sélecteur qui tourne sans fin).
// On (re)définit donc la carte dans le registre actif pendant 30 s.
const CARD_TAG = "cantine-eda-card";
const EDITOR_TAG = "cantine-eda-card-editor";

function defineCard() {
  const registry = window.customElements;
  try {
    // Des sous-classes neuves évitent l'erreur « constructor already used ».
    if (!registry.get(EDITOR_TAG)) registry.define(EDITOR_TAG, class extends CantineEdaCardEditor {});
    if (!registry.get(CARD_TAG)) registry.define(CARD_TAG, class extends CantineEdaCard {});
  } catch (err) {
    console.warn(`${CARD_TAG}:`, err);
  }
  return !!registry.get(CARD_TAG);
}

const firstLoad = !window.__cantineEdaCardLoaded;
window.__cantineEdaCardLoaded = true;
defineCard();
let defineTries = 0;
const defineWatcher = setInterval(() => {
  defineCard();
  if (++defineTries >= 60) clearInterval(defineWatcher);
}, 500);

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === CARD_TAG)) {
  window.customCards.push({
    type: CARD_TAG,
    name: "Cantine EDA",
    description: "Solde, passages, photo du plateau et rechargement du compte cantine",
    preview: true,
    documentationURL: "https://github.com/globoudou/home-assistant-cantine-eda",
  });
}
if (firstLoad) {
  console.info(`%c CANTINE-EDA-CARD %c ${CARD_VERSION} `, "background:#e8710a;color:#fff;border-radius:4px 0 0 4px", "background:#444;color:#fff;border-radius:0 4px 4px 0");
}
