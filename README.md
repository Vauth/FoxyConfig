# FoxyVPN Config
This repository hosts [`config.json`](config.json), which dynamically supplies HTTP headers, user agents, and protocol configurations across all client network components. Whenever Mozilla or Fastly updates rules, headers, or challenge mechanisms, updating this repository instantly updates all installed clients without requiring an app update or rebuild.

<br>

## Delivery Endpoints

The FoxyVPN client automatically pulls configuration with built-in CDN redundancy:

| Tier | Provider | URL |
| :--- | :--- | :--- |
| **Primary** | GitHub Raw | `https://raw.githubusercontent.com/Vauth/FoxyConfig/main/config.json` |
| **Fallback** | jsDelivr CDN | `https://cdn.jsdelivr.net/gh/Vauth/FoxyConfig@main/config.json` |

