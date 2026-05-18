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
