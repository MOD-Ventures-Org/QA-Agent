# Product Context — ARIA Test Generator

Fill in the sections below. ARIA reads this file before generating tests so it understands your product and produces relevant, accurate test cases.

---

## App Overview

<!-- What does this product do? Who are the users? -->
Example: A B2B SaaS platform that lets teams manage projects and track tasks. Used by project managers and developers.

## Tech Stack

<!-- Frontend framework, backend language, database, auth method -->
- Frontend: React / Next.js
- Backend: Node.js / Python FastAPI
- Database: PostgreSQL / MongoDB
- Auth: JWT / OAuth / Session-based

## Pages & Routes

<!-- List the main pages and their URLs -->
- `/` — Landing / Home
- `/login` — Login page
- `/register` — Registration
- `/dashboard` — Main dashboard after login
- `/settings` — User settings

## Key Features

<!-- List the core features users interact with -->
- User registration and login
- Dashboard with overview metrics
- Create / edit / delete items
- Search and filter
- Notifications

## User Flows

<!-- Describe the critical paths a user takes -->
1. **Sign up flow:** User visits /register → fills form → verifies email → lands on dashboard
2. **Login flow:** User visits /login → enters credentials → redirected to dashboard
3. **Core action flow:** User logs in → navigates to feature → performs action → sees result

## API Endpoints

<!-- List key API endpoints -->
- `POST /api/auth/login` — Login
- `POST /api/auth/register` — Register
- `GET /api/user/me` — Get current user
- `GET /api/items` — List items
- `POST /api/items` — Create item
- `PUT /api/items/:id` — Update item
- `DELETE /api/items/:id` — Delete item

## Known Constraints

<!-- Edge cases, business rules, or things testers should know -->
- Passwords must be at least 8 characters
- Email must be verified before login
- Only admins can delete items
- Rate limit: 100 requests per minute per user

## UI Test Conventions (Playwright)

<!--
This section steers HOW ARIA writes the generated Playwright UI tests — selectors,
navigation, and the login flow. Fill in your product's real conventions; anything
you leave as-is below is used as the default. The generated `page` fixture and
`base_url` come from conftest; tests navigate with `page.goto(f"{base_url}/path")`.
-->

### Selector strategy (most preferred first)
1. Role + accessible name: `page.get_by_role("button", name="Sign in")`
2. Label: `page.get_by_label("Email")`
3. Visible text: `page.get_by_text("Welcome back")`
4. Test id: `page.get_by_test_id("submit")` — our test-id attribute is `data-testid`
Avoid brittle CSS/XPath and nth-child selectors. Avoid selecting by CSS class.

### Waiting & assertions
- Use Playwright auto-waiting assertions: `from playwright.sync_api import expect` then
  `expect(locator).to_be_visible()` / `expect(page).to_have_url(...)`.
- Never use `time.sleep()` or fixed timeouts for synchronization.

### Authentication (how a test logs in before exercising a protected page)
<!-- Describe the exact steps so ARIA reproduces your real login flow. -->
1. `page.goto(f"{base_url}/login")`
2. `page.get_by_label("Email").fill("test@example.com")`
3. `page.get_by_label("Password").fill("Password123")`
4. `page.get_by_role("button", name="Log in").click()`
5. `expect(page).to_have_url(f"{base_url}/dashboard")`

### Test data & things to avoid
- Use disposable/seeded test accounts; never hardcode real user credentials.
- Don't assert on auto-generated ids, timestamps, or copy that changes often.
