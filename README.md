# FoxyVPN Config

This repository hosts [`config.json`](config.json), which dynamically supplies HTTP headers, user agents, and protocol configurations across all client network components. Whenever Mozilla or Fastly updates rules, headers, or challenge mechanisms, updating this repository instantly updates all installed clients without requiring an app update or rebuild.

<br>

## Delivery Endpoints

The FoxyVPN client automatically pulls configuration with built-in CDN redundancy:

| Tier | Provider | URL |
| :--- | :--- | :--- |
| **Primary** | GitHub Raw | `https://raw.githubusercontent.com/Vauth/FoxyConfig/main/config.json` |
| **Fallback** | jsDelivr CDN | `https://cdn.jsdelivr.net/gh/Vauth/FoxyConfig@main/config.json` |

<br>

## Configuration Schema

The configuration file is structured into modular components matching the client's internal network layers:

| Field | Type | Description |
| :--- | :--- | :--- |
| `version` | `integer` | Schema version identifier (e.g. `1`). |
| `controlPlane` | `object` | Headers and protocols for Firefox Accounts authentication and OAuth requests. |
| `guardian` | `object` | Headers for Mozilla Guardian VPN token provisioning (`/api/v1/fpn/*`). |
| `solver` | `object` | Headers for the automated Fastly bot challenge solver (proof-of-work, PAT, post-backs). |
| `browser` | `object` | Headers (primarily `User-Agent`) applied to the in-app authentication browser (WebView). |

<br>

## Dynamic Template Variables

Client apps dynamically interpolate device-specific parameters into header values at runtime (case-insensitive):

| Variable | Aliases | Replaced With | Example |
| :--- | :--- | :--- | :--- |
| `{android_version}` | `${android_version}`, `{os_version}`, `${os_version}` | Device's Android OS release string | `14`, `15`, `13` |
| `{android_sdk}` | `${android_sdk}` | Device's Android API SDK level | `34`, `35`, `33` |

> [!TIP]
> If a header string contains no template variables, it is used as a static literal without modification.

<br>


## How the Client Handles Updates

1. **First Launch & App Start**: The client checks for an updated configuration asynchronously immediately upon opening (both before and after authentication).
2. **App Resume**: Configuration refresh is triggered each time the user brings the app to the foreground.
3. **Self-Healing on HTTP 406**: If Mozilla's edge bot-protection challenges or rejects an active request with HTTP 406, the client triggers a remote configuration refresh automatically.
4. **Offline Resilience**: The client stores the last successfully fetched configuration atomically on disk. If the network or remote repository is unreachable, it seamlessly falls back to the saved offline configuration, or to the local offline defaults.

<br>

## Privacy & Safety

- **Public Strings Only**: This repository only distributes standard HTTP request headers and protocol settings.
- **No Executable Code**: The configuration file is pure JSON data parsed strictly by key; it cannot run scripts or executable instructions.
- **Zero Telemetry**: The client performs a standard anonymous HTTP GET request to GitHub/jsDelivr and transmits zero user credentials, identifiers, or usage analytics.
```
