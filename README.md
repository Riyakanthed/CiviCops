# CiviCops

A civic issue reporting application with a React web interface, an Expo mobile app, and a Python/FastAPI + MongoDB API. Accounts use email/password authentication and signed JWT sessions. There is no third-party sign-in.

## Configure account domains and secrets

The backend reads `backend/.env`. Keep the existing MongoDB URI and other local settings. Configure these values there:

- `AUTH_ADMIN_DOMAIN`: exact email domain for Admin accounts.
- `AUTH_WORKER_DOMAIN`: exact email domain for Worker accounts.
- `AUTH_CITIZEN_DOMAIN`: exact email domain for Citizen accounts.
- `JWT_SECRET`: a private random secret of at least 32 bytes. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
- `BOOTSTRAP_ADMIN_TOKEN`: a separate private, random, one-time code used to create the first Admin account.

The example configuration uses `admin.example.org`, `staff.example.org`, and `example.org`. Replace these with the actual email domains you control before use. Domains match exactly; a subdomain does not match its parent domain. Emails on any other domain cannot register.

The first Admin registers with an email on the configured Admin domain and the bootstrap token. The token stops working once the first password-enabled Admin exists. After that, Admins create account invitations from the **Team** page. Invitations are tied to one email and its matching role domain, expire after 72 hours, and can be used once. Worker invitations include a department.

New Citizen-domain registrations are allowed without an invitation. Staff accounts require invitations. This app does not verify email ownership: protect the bootstrap code and only share invitations with the intended recipient. Anyone who can register an unclaimed email address on an enabled Citizen domain can act as that account, so add email verification before opening public registration to untrusted users.

Passwords must be at least 10 characters and are stored as salted scrypt hashes. JWT sessions expire after one hour. Existing MongoDB user/report records are retained; accounts created under the old sign-in flow must receive a one-time invitation before setting a local password.

Do not send secrets or invitation codes in chat or commit local environment files. The `.env.example` files contain placeholders only.

## Run the web app

```powershell
npm install
npm run dev
```

The web app is available at `http://localhost:5173`. The full issue map uses Leaflet and OpenStreetMap tiles and needs an internet connection to load map tiles.

## Run the citizen mobile app

In another terminal:

```powershell
cd mobile
npm install
npm start
```

The mobile app uses the same email/password API and stores its JWT using Expo SecureStore. For a physical phone, set `EXPO_PUBLIC_API_URL` in `mobile/.env` to the computer's reachable LAN address (for example `http://192.168.1.20:8000/api`); `localhost` is for a local simulator. Staff accounts should use the web dashboard.

## Run the API

The backend reads settings from `backend/.env`. Then:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

API docs are at `http://localhost:8000/docs`. The API starts without resolving MongoDB; if Atlas DNS or the network is unavailable, `/api/health` reports a degraded database and database-dependent API requests return HTTP 503 while retrying the connection on later requests. Reports, accounts, invitations, notifications, report upvotes, and report images are stored in MongoDB (images use GridFS). User documents are in the `users` collection; the web and mobile apps store only the signed-in session token locally and load account details from the API. In MongoDB Atlas, select the cluster configured by `backend/.env`, then the database named by `MONGO_DB` (defaults to `civicops`) and collection `users`. Account documents appear after registration. Admins can also view registered citizen, worker, and admin accounts on the web dashboard’s **Team** page.

In the web report form, click **Get my current location** and allow the browser permission prompt; latitude and longitude then fill automatically. Browser GPS requires HTTPS, or `http://localhost:5173` when using the same computer that runs the web app. Plain HTTP on a LAN address (such as `http://192.168.x.x`) cannot request browser location; use HTTPS for access from another device. If permission was previously denied, allow Location for this site in the browser's site settings and try again. The form also accepts manually entered coordinates. Click **Record** to dictate report details into the description; allow microphone access when prompted. Speech recognition requires a supported browser (such as current Chrome or Edge) and an internet connection; only the transcript is included in the report, not an audio recording. An optional street or landmark is saved as a description and is not converted into coordinates. Community members can upvote another citizen’s open report once. Each upvote adds one point to its priority score, capped at a 20-point boost, and the admin report list sorts by the resulting priority. Admins still decide and record report status changes. Admin analytics and department workload are calculated from report records, including recorded status-history timestamps.

## Roles

- **Citizen:** self-registers from the exact configured Citizen email domain; can submit and track their own reports, and browse other residents' reports and public status updates in Explore and the community map. Public views omit reporter identity and staff-only status history details.
- **Worker:** registered using an Admin-issued invitation for the exact Worker email domain; can see assigned reports and mark them In progress or Resolved.
- **Admin:** the first account is created with the bootstrap token; later Admin accounts require an invitation for the exact Admin email domain. Admins can view reports, issue invitations, and assign reports to workers in the matching department.

API role headers are not accepted as authentication. Roles are derived from exact configured domains at registration and persisted in MongoDB.
