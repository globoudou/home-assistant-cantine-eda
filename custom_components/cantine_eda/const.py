"""Constantes de l'intégration Moneweb Cantine."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "cantine_eda"
MANUFACTURER: Final = "Moneweb"

# --- Données de l'entrée (config entry data) -------------------------------
CONF_HOST: Final = "host"
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"
CONF_VERIFY_SSL: Final = "verify_ssl"
# Mot de passe précédent (conservé après un renouvellement, pour pouvoir le récupérer)
CONF_PREVIOUS_PASSWORD: Final = "previous_password"
# Mot de passe en cours de changement (écrit AVANT l'appel au site, pour ne
# jamais perdre un mot de passe si Home Assistant s'arrête au mauvais moment)
CONF_PENDING_PASSWORD: Final = "pending_password"
CONF_PASSWORD_CHANGED_AT: Final = "password_changed_at"

# --- Options ----------------------------------------------------------------
OPT_SCAN_INTERVAL: Final = "scan_interval"
OPT_LOW_BALANCE: Final = "low_balance_threshold"
OPT_RECHARGE_URL: Final = "recharge_url"
# Autoriser les utilisateurs non administrateurs à copier le mot de passe depuis la carte
OPT_ALLOW_NON_ADMIN: Final = "allow_non_admin"
OPT_PHOTO_URL_TEMPLATE: Final = "photo_url_template"
OPT_RENEW_DAYS: Final = "renew_interval_days"
OPT_PASSWORD_LENGTH: Final = "password_length"
OPT_PASSWORD_SYMBOLS: Final = "password_symbols"
OPT_CHANGE_ENDPOINT: Final = "password_change_endpoint"
OPT_CHANGE_PAYLOAD: Final = "password_change_payload"

DEFAULT_HOST: Final = "eda-arcueil.moneweb.fr"
DEFAULT_SCAN_INTERVAL: Final = 10  # minutes
DEFAULT_LOW_BALANCE: Final = 5.0
DEFAULT_RENEW_DAYS: Final = 0  # 0 = renouvellement automatique désactivé
DEFAULT_PASSWORD_LENGTH: Final = 16
DEFAULT_PASSWORD_SYMBOLS: Final = True
# Endpoint de changement de mot de passe : inconnu à ce jour, à confirmer
# avec les outils développeur du navigateur (voir README).
DEFAULT_CHANGE_ENDPOINT: Final = ""
DEFAULT_CHANGE_PAYLOAD: Final = (
    '{"OldPassword": "{old}", "NewPassword": "{new}", "ConfirmPassword": "{new}"}'
)

# --- API Moneweb -------------------------------------------------------------
API_BASE: Final = "/clients/api"
API_SIGN_IN: Final = f"{API_BASE}/sign/in"
API_DASHBOARD: Final = f"{API_BASE}/compte/dashboard"
CLIENT_PATH: Final = "/clients/"

# --- Divers -------------------------------------------------------------------
SERVICE_GET_PASSWORD: Final = "get_password"
SERVICE_RENEW_PASSWORD: Final = "renew_password"
SERVICE_SET_PASSWORD: Final = "set_password"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"

CARD_FILENAME: Final = "cantine-eda-card.js"
CARD_URL: Final = f"/{DOMAIN}/{CARD_FILENAME}"

RENEW_CHECK_INTERVAL_MINUTES: Final = 60
ISSUE_RENEW_FAILED: Final = "renew_failed"
