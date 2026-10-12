# PayrollKit UI brief

A prompt for a design tool, plus notes on how the finished UI uses this API. The UI lives in the
[`payroll-kit-ui`](https://github.com/oktoberlin/payroll-kit-ui) repository, at `payroll.hookangles.com`; the API is
at `api.payroll.hookangles.com`.

## Why it should look different from the other demo apps

The invoice, reply and handoff UIs are all light-only, with warm or cool off-white backgrounds
(`#f7f6f2`, `#f4f6f4`), Geist and Geist Mono fonts, a lime or green accent (`oklch(0.88 0.19 122)`, hue 145
for handoff), hairline-border cards, a thin `Header` and a `Credit` footer, and single-column "desk" layouts.
They share the stack Next.js 15, React 19 and Tailwind 3.4. PayrollKit is a dense, numbers-heavy admin
tool, so it takes the opposite direction: a dark app shell with a sidebar, a different typeface, and a
cool indigo/amber palette.

## Prompt

```
Design the web UI for "PayrollKit", a multi-tenant payroll admin app (demo, fake data). It is a
desktop-first, data-dense business tool, not a marketing site. Deliver high-fidelity mockups for
the screens below, in light AND dark mode, plus a small design-system sheet (tokens + components).

## Look and feel (important: must look clearly different from my other demo apps)
My other apps are all light-only, warm off-white (#f7f6f2) backgrounds, Geist font, lime-green accent,
single-column pages with a thin top header. DO NOT reuse any of that. For PayrollKit use instead:
- An app shell: fixed left sidebar (collapsible to icons) + top bar with company switcher label,
  breadcrumb, and user menu. Sidebar is a deep ink/slate (e.g. #0f1424), content area is light cool-grey
  in light mode and near-black navy in dark mode.
- Palette: deep indigo as the primary (not green/lime), amber for warnings/"draft", emerald only for
  "paid/success", rose for errors. Cool neutral greys. Design dark mode as a first-class theme.
- Typography: a different family from Geist, e.g. "IBM Plex Sans" for UI and "IBM Plex Mono" for all
  money and IDs. Every money value uses tabular, right-aligned monospace numerals.
- Density: compact tables (36-40px rows), sticky headers, zebra-free with hairline row dividers,
  keyboard-friendly. Think accounting software / finance dashboard, calm and trustworthy.
- Status "pills" with a leading dot: Draft (amber), Approved (indigo), Paid (emerald), plus a lock icon
  on Paid items to signal immutability.
- A persistent small footer note on any screen showing tax figures: "Illustrative tax table. Not tax advice."

## Users and roles (3 logins, same password "demo-pass-123")
- demo_admin    : full access, can Approve, Reopen, Mark paid, and read the Audit log.
- demo_manager  : manages employees/periods/timesheets, can Calculate, CANNOT approve or mark paid
                  (show those buttons disabled with a tooltip "Admin only", not hidden).
- demo_employee : sees only "My payslips" (own payslips + PDF). No sidebar items for admin areas.
The login screen should show the three demo accounts as one-click "Sign in as" cards, and a visible
"All data is fake" notice. Company in the demo: "Northwind Demo Co", currency USD.

## Screens to design
1. Login (with the demo-account cards).
2. Dashboard (admin/manager): current pay period card, next pay date, headcount, latest run status,
   total gross / total net of latest run, a small "payroll cost by employee" bar chart, recent audit events.
3. Payroll runs list: table with Period, Pay date, Status pill, Payslips count, Calculated at, Net total,
   row action menu. "New run" button (select a pay period).
4. Payroll run detail (the hero screen):
   - Header: period, pay date, status pill, and a horizontal stepper Draft -> Approved -> Paid.
   - Action bar: Calculate, Approve, Reopen (only from Approved), Mark paid, Export CSV register.
     Buttons enable/disable by status and role. Show a toast "Nothing changed, no recalculation needed"
     when Calculate returns the X-Recalculated: false header, and "Recalculated N payslips" otherwise.
   - A warning banner state: "Inputs changed since the last calculation. Recalculate before approving."
   - Table of payslips: Employee, Gross, Pre-tax, Taxable, Tax, Post-tax, Net, Employer benefits (muted);
     totals row pinned at the bottom; row click opens a right-hand drawer.
   - Paid state: whole page gets a calm "locked" treatment (lock icon, read-only, no actions except export/PDF).
5. Payslip drawer / detail page: employee header, itemised lines grouped as Earnings, Pre-tax deductions,
   Tax, Post-tax deductions, Employer benefits (shown but marked "not deducted"), then a clear
   reconciliation: Gross - Pre-tax - Tax - Post-tax = Net. Year-to-date block (gross, tax, net).
   "Download PDF" button. Include the worked example below.
6. Employees list + detail/edit form: filters (pay type, active), CSV import button.
7. CSV import: drag-and-drop zone, then a result state that shows row-level errors in a table
   ("Row 3: duplicate employee_number A1") and the all-or-nothing message "No employees were imported".
8. Pay periods + Timesheets: periods table, and a timesheet grid (employee x regular/overtime hours)
   with inline editing for hourly employees.
9. Deductions & benefits setup: list of deduction types (code, name, kind deduction/benefit,
   method fixed/percent, default value, pre-tax toggle) and assignment to employees with optional override.
10. Tax tables: table with editable brackets (lower, upper or "no cap", rate %), validation messages, and a
    permanent "Illustrative only" badge.
11. Audit log (admin only): filterable timeline/table (action, object type, actor, time, expandable JSON diff).
12. My payslips (employee role): list of own payslips, simple detail, PDF download. Friendly and minimal.
13. States for every list/form: loading skeletons, empty states with a helpful CTA, inline field errors,
    403 "You do not have access" page, 409 "Cannot delete, it is still in use" message, and session-expired.

## Data to use in the mockups (fake, consistent)
Company: Northwind Demo Co (USD). Period: 2026-09-01 to 2026-09-30, pay date 2026-10-01, monthly.
Run: status Draft, 6 payslips. Employees:
E001 Ava Stone (salary 72,000) / E002 Ben Carter (salary 54,000, has a 50.00 loan repayment)
E003 Chloe Diaz (salary 96,000) / E004 Dev Patel (hourly 22.50, 160h + 12h overtime)
E005 Ella Moore (hourly 18.75, 120h) / E006 Finn Lee (hourly 31.00, 168h + 6.5h overtime)
Deduction types: RET Retirement 5% (pre-tax), HEALTH Health plan 120.00 (pre-tax),
LOAN Salary advance 50.00 (post-tax), MATCH Employer retirement match 3% (benefit, not deducted).
Worked payslip for E001 Ava Stone (monthly):
  Earnings: Salary 6,000.00 -> Gross 6,000.00
  Pre-tax: RET 300.00, HEALTH 120.00 -> Taxable 5,580.00
  Tax (illustrative table): 690.93
  Post-tax: none
  Net: 4,889.07
  Employer benefit (not deducted): MATCH 180.00
All amounts show exactly 2 decimals with thousands separators and the "$" only in headers/totals.

## Interaction and content rules
- Money is never a float in the UI: display strings exactly as the API returns them.
- Destructive or irreversible actions (Approve, Mark paid) use a confirm dialog that states the
  consequence ("Paid runs cannot be changed").
- Show who did what: "Approved by demo_admin, 12 Oct 10:42".
- Responsive: desktop first, but the run detail and employee payslips must work at 390px wide
  (tables become cards or horizontally scroll with a pinned first column).
- Accessible: WCAG AA contrast in both themes, visible focus rings, status never conveyed by color alone.

## Deliverables
- Screens listed above, desktop (1440) in light and dark; mobile (390) for Login, Run detail, My payslips.
- Design tokens (colors, type scale, spacing, radius, shadows) as CSS variables I can paste.
- Component sheet: buttons, status pill, stat card, data table, drawer, stepper, toast, confirm dialog,
  form fields, file dropzone, empty state, skeleton.
- Keep it implementable in Next.js 15 + React 19 + Tailwind 3.4 (no exotic libraries), icons from lucide.
```

If the design tool only accepts short prompts, send the "Look and feel", "Users and roles" and "Data"
sections plus screens 1 to 5 first, then the remaining screens in follow-up rounds.

## How the UI uses this API

- **Auth:** the API uses JWT (`POST /api/auth/token/`; access token 30 min, refresh 1 day). The Next.js server holds
  the tokens in httpOnly cookies and proxies `/api/*` to the API, so the browser stays same-origin and the API needs
  no CORS configuration. The proxy also forwards the real visitor IP with a shared secret (`PROXY_IP_SECRET`), which
  the API uses for rate limiting.
- **Deploy:** the UI is a Vercel project at `payroll.hookangles.com` with `API_ORIGIN` set to
  `https://api.payroll.hookangles.com`. The API stays on the VPS (see `deploy/SETUP.md`).
- **API facts the UI relies on:**
  - `GET /api/auth/me/` returns the user, role, company and (for employees) their employee record.
  - Runs include `period`, exact `totals`, `is_stale`, and `calculated_by` / `approved_by` / `paid_by` usernames.
  - `calculate` returns the header `X-Recalculated: true|false`; a failed calculation names the employee.
  - Errors: 400 for validation and calculation problems, 403 for the wrong role, 409 when deleting something
    still referenced. Duplicate employee numbers and deduction codes are 400 field errors.
  - `GET /api/payslips/{id}/` includes `ytd`; the list does not. Employees only see approved or paid payslips.
  - Audit entries carry `object_label` and `{old, new}` changes.
  - Money values arrive as strings and are displayed as returned; never converted to floats.
