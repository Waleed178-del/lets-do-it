# 02. Chain of Command (all 91 roles)

Generated from `plugins/hive/skills/build/engine/roster.json`, the single source of truth. Regenerate after editing the roster with `python3 docs/system/generate_hierarchy.py`.

## Top level

```mermaid
flowchart TD
    O["Orchestrator<br/>your Claude Code session"]
    O --> mgr_product["Product & Discovery Manager<br/>phase 1-discovery"]
    O --> mgr_architecture["Architecture & Foundations Manager<br/>phase 2-architecture"]
    O --> mgr_backend["Backend & Data Manager<br/>phase 3-build"]
    O --> mgr_frontend["Web Frontend Manager<br/>phase 3-build"]
    O --> mgr_mobile["Mobile Apps Manager (conditional)<br/>phase 3-build"]
    O --> mgr_quality["Quality & Security Manager<br/>phase 4-quality"]
    O --> mgr_release["DevOps & Release Manager<br/>phase 5-release"]
    classDef orch fill:#1f2937,color:#fff,stroke:#111
    classDef mgr fill:#2563eb,color:#fff,stroke:#1e40af
    class O orch
    class mgr_product,mgr_architecture,mgr_backend,mgr_frontend,mgr_mobile,mgr_quality,mgr_release mgr
```

## Headcount

| Level | Role | Count |
|---|---|---|
| 0 | Orchestrator | 1 |
| 1 | Manager | 7 |
| 2 | Supervisor | 18 |
| 3 | Worker | 65 |
| | **Total** | **91** |

## Product & Discovery Manager (`mgr-product`)

Turn the raw plan into an unambiguous, testable product specification and UX definition that every downstream team can build against without guessing.

```mermaid
flowchart LR
    mgr_product["Product & Discovery Manager"]
    mgr_product --> sup_requirements["Requirements Supervisor"]
    sup_requirements --> wrk_plan_parser(["Plan Parser"])
    sup_requirements --> wrk_user_stories(["User Story Writer"])
    sup_requirements --> wrk_acceptance_criteria(["Acceptance Criteria Writer"])
    sup_requirements --> wrk_scope_risk(["Scope & Risk Analyst"])
    mgr_product --> sup_ux["UX Definition Supervisor"]
    sup_ux --> wrk_sitemap_ia(["Information Architect"])
    sup_ux --> wrk_user_flows(["User Flow Designer"])
    sup_ux --> wrk_design_spec(["Visual Design Specifier"])
    sup_ux --> wrk_copywriter(["Product Copywriter"])
    classDef mgr fill:#2563eb,color:#fff,stroke:#1e40af
    classDef sup fill:#7c3aed,color:#fff,stroke:#5b21b6
    classDef wrk fill:#ecfdf5,color:#064e3b,stroke:#059669
    class mgr_product mgr
    class sup_requirements,sup_ux sup
    class wrk_plan_parser,wrk_user_stories,wrk_acceptance_criteria,wrk_scope_risk,wrk_sitemap_ia,wrk_user_flows,wrk_design_spec,wrk_copywriter wrk
```

| Supervisor | Worker | Task |
|---|---|---|
| `sup-requirements` | `wrk-plan-parser` | Extract every feature, constraint, persona, platform and non functional requirement from .hive/plan.md into a numbered requirements list with IDs (REQ-001...). |
| `sup-requirements` | `wrk-user-stories` | Write user stories (US-001...) in the form As a, I want, so that, each linked to its REQ IDs and prioritized MoSCoW. |
| `sup-requirements` | `wrk-acceptance-criteria` | Write Given When Then acceptance criteria (AC-001...) for every user story, each one objectively testable. |
| `sup-requirements` | `wrk-scope-risk` | Record scope boundaries, assumptions taken for ambiguous points, dependencies and a risk register with mitigations. |
| `sup-ux` | `wrk-sitemap-ia` | Define the sitemap and screen inventory per platform: every page or screen, its route, purpose, sections, data needed and access level. |
| `sup-ux` | `wrk-user-flows` | Document step by step flows for every primary task including error, empty and loading states, as Mermaid diagrams plus text. |
| `sup-ux` | `wrk-design-spec` | Specify the visual system: palette with contrast ratios, typography scale, spacing, radii, elevation, breakpoints and component patterns. |
| `sup-ux` | `wrk-copywriter` | Write final copy for every page: headings, body, CTAs, form labels, validation and error messages, empty states and emails. |

## Architecture & Foundations Manager (`mgr-architecture`)

Choose the stack, define the data model and API contract, scaffold a repository that installs, builds and tests green, and publish the ownership map and command map every later dispatch depends on.

```mermaid
flowchart LR
    mgr_architecture["Architecture & Foundations Manager"]
    mgr_architecture --> sup_system_design["System Design Supervisor"]
    sup_system_design --> wrk_stack_selector(["Stack Selector"])
    sup_system_design --> wrk_data_modeler(["Data Modeler"])
    sup_system_design --> wrk_api_contract(["API Contract Designer"])
    sup_system_design --> wrk_ownership_mapper(["Ownership & Command Mapper"])
    mgr_architecture --> sup_scaffold["Scaffolding Supervisor"]
    sup_scaffold --> wrk_env_provisioner(["Environment Provisioner"])
    sup_scaffold --> wrk_repo_scaffolder(["Repository Scaffolder"])
    sup_scaffold --> wrk_tooling_config(["Tooling Configurator"])
    sup_scaffold --> wrk_env_config(["Environment Configurator"])
    classDef mgr fill:#2563eb,color:#fff,stroke:#1e40af
    classDef sup fill:#7c3aed,color:#fff,stroke:#5b21b6
    classDef wrk fill:#ecfdf5,color:#064e3b,stroke:#059669
    class mgr_architecture mgr
    class sup_system_design,sup_scaffold sup
    class wrk_stack_selector,wrk_data_modeler,wrk_api_contract,wrk_ownership_mapper,wrk_env_provisioner,wrk_repo_scaffolder,wrk_tooling_config,wrk_env_config wrk
```

| Supervisor | Worker | Task |
|---|---|---|
| `sup-system-design` | `wrk-stack-selector` | Select languages, frameworks, database, hosting and mobile approach that fit the plan and record the decision with alternatives and tradeoffs. Prefer mainstream, well supported, current stable versions. |
| `sup-system-design` | `wrk-data-modeler` | Design entities, fields, types, relations, indexes and constraints that serve every user story. Include an ER diagram in Mermaid. |
| `sup-system-design` | `wrk-api-contract` | Specify every endpoint: method, path, auth, request and response schemas, status codes and error format. Write OpenAPI 3.1 YAML plus a readable summary. |
| `sup-system-design` | `wrk-ownership-mapper` | Write ownership.json mapping every code owning Hive role ID to the path globs it may change (plus _shared and _ignore lists), commands.json with install, build, lint, typecheck, test, e2e and per platform commands for the chosen stack, and toolchain.json listing every required tool as {bin, version_cmd, min, install_hint, optional}. |
| `sup-scaffold` | `wrk-env-provisioner` | Make every tool in docs/architecture/toolchain.json available: detect what is installed, install missing tools non interactively with user level installers where possible (for example nvm, corepack, pipx, sdkman, the Android command line tools), and verify with `hive doctor --project`. Never use sudo unless it works without a password. Report BLOCKED with the exact install command when a tool needs elevated rights or a different operating system. |
| `sup-scaffold` | `wrk-repo-scaffolder` | Generate the project skeleton exactly per stack.md and the ownership map directory layout, using official generators non interactively where they exist. |
| `sup-scaffold` | `wrk-tooling-config` | Configure linting, formatting, type checking and the test runner, each with a script matching commands.json, plus one passing sample test. |
| `sup-scaffold` | `wrk-env-config` | Implement typed configuration loading with validation, .env.example listing every variable with comments, and safe defaults for local development. |

## Backend & Data Manager (`mgr-backend`)

Deliver the server side: persistence, API, authentication and third party integrations, exactly matching the API contract, with tests.

```mermaid
flowchart LR
    mgr_backend["Backend & Data Manager"]
    mgr_backend --> sup_data["Data Layer Supervisor"]
    sup_data --> wrk_schema_migrator(["Schema & Migration Engineer"])
    sup_data --> wrk_seed_data(["Seed Data Engineer"])
    sup_data --> wrk_repository_layer(["Data Access Engineer"])
    mgr_backend --> sup_api["API Supervisor"]
    sup_api --> wrk_endpoint_builder(["Endpoint Builder"])
    sup_api --> wrk_validation(["Input Validation Engineer"])
    sup_api --> wrk_error_handling(["Error Handling Engineer"])
    sup_api --> wrk_api_docs(["API Documentation Engineer"])
    mgr_backend --> sup_auth["Auth & Access Supervisor"]
    sup_auth --> wrk_auth_flow(["Authentication Engineer"])
    sup_auth --> wrk_rbac(["Authorization Engineer"])
    sup_auth --> wrk_session_security(["Session Security Engineer"])
    mgr_backend --> sup_integrations["Integrations Supervisor"]
    sup_integrations --> wrk_payments(["Payments Integrator"])
    sup_integrations --> wrk_email_notify(["Email & Notifications Integrator"])
    sup_integrations --> wrk_file_storage(["File Storage Integrator"])
    sup_integrations --> wrk_third_party(["Third Party API Integrator"])
    classDef mgr fill:#2563eb,color:#fff,stroke:#1e40af
    classDef sup fill:#7c3aed,color:#fff,stroke:#5b21b6
    classDef wrk fill:#ecfdf5,color:#064e3b,stroke:#059669
    class mgr_backend mgr
    class sup_data,sup_api,sup_auth,sup_integrations sup
    class wrk_schema_migrator,wrk_seed_data,wrk_repository_layer,wrk_endpoint_builder,wrk_validation,wrk_error_handling,wrk_api_docs,wrk_auth_flow,wrk_rbac,wrk_session_security,wrk_payments,wrk_email_notify,wrk_file_storage,wrk_third_party wrk
```

| Supervisor | Worker | Task |
|---|---|---|
| `sup-data` | `wrk-schema-migrator` | Implement the schema and migrations from data-model.md, with indexes and constraints, applying cleanly from an empty database. |
| `sup-data` | `wrk-seed-data` | Create idempotent seed scripts with realistic development data covering every entity and edge case needed by the UI. |
| `sup-data` | `wrk-repository-layer` | Implement the repository or data access layer for every entity with unit tests against a test database or in memory equivalent. |
| `sup-api` | `wrk-endpoint-builder` | Implement the assigned endpoints exactly per api-contract.md with tests for success and failure paths. |
| `sup-api` | `wrk-validation` | Add schema based input validation to every endpoint with precise error messages and tests for invalid input. |
| `sup-api` | `wrk-error-handling` | Implement a consistent error model, central error handler, structured logging of failures and 404 and 500 handling, with tests. |
| `sup-api` | `wrk-api-docs` | Serve or generate API reference docs from the implemented API and verify they match openapi.yaml. |
| `sup-auth` | `wrk-auth-flow` | Implement the required authentication flows with salted hashing or a vetted provider, email verification and reset where required, with tests. |
| `sup-auth` | `wrk-rbac` | Implement roles, permissions and ownership checks on every protected resource, with tests that prove denial. |
| `sup-auth` | `wrk-session-security` | Harden sessions or tokens: secure cookies, expiry, rotation, CSRF protection, rate limiting on auth endpoints and security headers. |
| `sup-integrations` | `wrk-payments` | Integrate the payment provider behind an adapter with webhook signature verification and idempotency, tested with a fake. |
| `sup-integrations` | `wrk-email-notify` | Implement transactional email and notifications behind an adapter with templates from copy.md and a console transport for development. |
| `sup-integrations` | `wrk-file-storage` | Implement file upload and storage behind an adapter with type and size validation and a local disk driver for development. |
| `sup-integrations` | `wrk-third-party` | Integrate any other external API the plan requires behind an adapter with timeouts, retries and a fake for tests. |

## Web Frontend Manager (`mgr-frontend`)

Deliver the complete web user interface: design system, every page in the sitemap, client state and API wiring, responsive and accessible.

```mermaid
flowchart LR
    mgr_frontend["Web Frontend Manager"]
    mgr_frontend --> sup_ui_system["UI System Supervisor"]
    sup_ui_system --> wrk_design_tokens(["Design Token Engineer"])
    sup_ui_system --> wrk_component_builder(["Component Builder"])
    sup_ui_system --> wrk_layout_shell(["Layout Shell Engineer"])
    sup_ui_system --> wrk_assets(["Icon & Asset Engineer"])
    mgr_frontend --> sup_pages["Pages Supervisor"]
    sup_pages --> wrk_page_builder(["Page Builder"])
    sup_pages --> wrk_form_builder(["Form Builder"])
    sup_pages --> wrk_content_integrator(["Content Integrator"])
    sup_pages --> wrk_seo_meta(["SEO Engineer"])
    mgr_frontend --> sup_client_state["Client State & Data Supervisor"]
    sup_client_state --> wrk_state_manager(["State Management Engineer"])
    sup_client_state --> wrk_api_client(["API Client Engineer"])
    sup_client_state --> wrk_routing_guard(["Routing & Guard Engineer"])
    classDef mgr fill:#2563eb,color:#fff,stroke:#1e40af
    classDef sup fill:#7c3aed,color:#fff,stroke:#5b21b6
    classDef wrk fill:#ecfdf5,color:#064e3b,stroke:#059669
    class mgr_frontend mgr
    class sup_ui_system,sup_pages,sup_client_state sup
    class wrk_design_tokens,wrk_component_builder,wrk_layout_shell,wrk_assets,wrk_page_builder,wrk_form_builder,wrk_content_integrator,wrk_seo_meta,wrk_state_manager,wrk_api_client,wrk_routing_guard wrk
```

| Supervisor | Worker | Task |
|---|---|---|
| `sup-ui-system` | `wrk-design-tokens` | Implement design-spec.md as code level tokens (CSS variables or theme config) including light and dark modes if specified. |
| `sup-ui-system` | `wrk-component-builder` | Build the assigned reusable components with all variants and states, keyboard support and ARIA, plus tests. |
| `sup-ui-system` | `wrk-layout-shell` | Build the app shell: header, navigation, footer, responsive layout grid and error boundary. |
| `sup-ui-system` | `wrk-assets` | Set up the icon system, favicon set, logo placeholder and optimized image handling. |
| `sup-pages` | `wrk-page-builder` | Build the assigned pages from sitemap.md using the component library, with loading, empty and error states. |
| `sup-pages` | `wrk-form-builder` | Build the assigned forms with client validation matching server validation, accessible errors and submit states. |
| `sup-pages` | `wrk-content-integrator` | Place final copy from copy.md into every page and remove all placeholder text. |
| `sup-pages` | `wrk-seo-meta` | Add titles, descriptions, Open Graph tags, canonical URLs, sitemap.xml, robots.txt and structured data where relevant. |
| `sup-client-state` | `wrk-state-manager` | Implement client and server state management with caching and invalidation per the chosen stack. |
| `sup-client-state` | `wrk-api-client` | Implement a typed API client that matches openapi.yaml, handles auth and errors uniformly, and is shared with mobile when the stack allows. |
| `sup-client-state` | `wrk-routing-guard` | Implement routing, route guards for authenticated and role restricted pages, and redirects, with tests. |

## Mobile Apps Manager (`mgr-mobile`)

Deliver the requested native or cross platform mobile apps that consume the same API contract as the web app. Prefer a cross platform stack (Expo / React Native) unless the plan demands native, because it can be verified on any host and shares the web API client.

```mermaid
flowchart LR
    mgr_mobile["Mobile Apps Manager"]
    mgr_mobile --> sup_android["Android Supervisor"]
    sup_android --> wrk_android_scaffold(["Android Scaffolder"])
    sup_android --> wrk_android_screens(["Android Screen Builder"])
    sup_android --> wrk_android_data(["Android Data Engineer"])
    mgr_mobile --> sup_ios["iOS Supervisor"]
    sup_ios --> wrk_ios_scaffold(["iOS Scaffolder"])
    sup_ios --> wrk_ios_screens(["iOS Screen Builder"])
    sup_ios --> wrk_ios_data(["iOS Data Engineer"])
    classDef mgr fill:#2563eb,color:#fff,stroke:#1e40af
    classDef sup fill:#7c3aed,color:#fff,stroke:#5b21b6
    classDef wrk fill:#ecfdf5,color:#064e3b,stroke:#059669
    class mgr_mobile mgr
    class sup_android,sup_ios sup
    class wrk_android_scaffold,wrk_android_screens,wrk_android_data,wrk_ios_scaffold,wrk_ios_screens,wrk_ios_data wrk
```

| Supervisor | Worker | Task |
|---|---|---|
| `sup-android` | `wrk-android-scaffold` | Scaffold the Android project per stack.md with build configuration, theming from design-spec.md and navigation. |
| `sup-android` | `wrk-android-screens` | Build the assigned Android screens with all states, following platform conventions. |
| `sup-android` | `wrk-android-data` | Implement the Android networking, persistence and auth token storage wired to the API contract. |
| `sup-ios` | `wrk-ios-scaffold` | Scaffold the iOS project per stack.md with build configuration, theming from design-spec.md and navigation. |
| `sup-ios` | `wrk-ios-screens` | Build the assigned iOS screens with all states, following Human Interface Guidelines. |
| `sup-ios` | `wrk-ios-data` | Implement the iOS networking, persistence and Keychain token storage wired to the API contract. |

## Quality & Security Manager (`mgr-quality`)

Prove the product works and is safe: test coverage across levels, security review, accessibility, performance and responsiveness, and fix what fails.

```mermaid
flowchart LR
    mgr_quality["Quality & Security Manager"]
    mgr_quality --> sup_testing["Testing Supervisor"]
    sup_testing --> wrk_unit_tester(["Unit Test Engineer"])
    sup_testing --> wrk_integration_tester(["Integration Test Engineer"])
    sup_testing --> wrk_e2e_tester(["End to End Test Engineer"])
    sup_testing --> wrk_bug_fixer(["Bug Fixer"])
    mgr_quality --> sup_security["Security Supervisor"]
    sup_security --> wrk_dependency_auditor(["Dependency Auditor"])
    sup_security --> wrk_secure_code_reviewer(["Secure Code Reviewer"])
    sup_security --> wrk_secrets_scanner(["Secrets Scanner"])
    mgr_quality --> sup_experience_audit["Accessibility & Performance Supervisor"]
    sup_experience_audit --> wrk_a11y_auditor(["Accessibility Auditor"])
    sup_experience_audit --> wrk_performance_auditor(["Performance Auditor"])
    sup_experience_audit --> wrk_responsive_checker(["Responsive Layout Checker"])
    sup_experience_audit --> wrk_visual_reviewer(["Visual Design Reviewer"])
    classDef mgr fill:#2563eb,color:#fff,stroke:#1e40af
    classDef sup fill:#7c3aed,color:#fff,stroke:#5b21b6
    classDef wrk fill:#ecfdf5,color:#064e3b,stroke:#059669
    class mgr_quality mgr
    class sup_testing,sup_security,sup_experience_audit sup
    class wrk_unit_tester,wrk_integration_tester,wrk_e2e_tester,wrk_bug_fixer,wrk_dependency_auditor,wrk_secure_code_reviewer,wrk_secrets_scanner,wrk_a11y_auditor,wrk_performance_auditor,wrk_responsive_checker,wrk_visual_reviewer wrk
```

| Supervisor | Worker | Task |
|---|---|---|
| `sup-testing` | `wrk-unit-tester` | Write unit tests for untested logic in the assigned area, targeting behavior from acceptance-criteria.md. |
| `sup-testing` | `wrk-integration-tester` | Write integration tests across API, database and auth for the assigned flows. |
| `sup-testing` | `wrk-e2e-tester` | Write end to end browser tests (Playwright) for the assigned user flows against a locally started app. |
| `sup-testing` | `wrk-bug-fixer` | Reproduce the assigned failing test or defect, find the root cause, fix it at the source and prove the fix with a test. Never weaken or skip a test to make it pass. |
| `sup-security` | `wrk-dependency-auditor` | Run the ecosystem audit tool, upgrade or replace vulnerable dependencies, and record residual findings. |
| `sup-security` | `wrk-secure-code-reviewer` | Review the code for OWASP Top 10 issues (injection, broken access control, XSS, SSRF, insecure deserialization, misconfiguration) and fix confirmed findings with tests. |
| `sup-security` | `wrk-secrets-scanner` | Scan the repository and git history for committed secrets and unsafe config, move values to environment variables and update .env.example. |
| `sup-experience-audit` | `wrk-a11y-auditor` | Audit core flows against WCAG 2.1 AA (automated axe checks plus manual keyboard and semantics review) and fix violations. |
| `sup-experience-audit` | `wrk-performance-auditor` | Measure bundle size and page performance, remove waste, add lazy loading and caching, and report before and after numbers. |
| `sup-experience-audit` | `wrk-responsive-checker` | Render every page at 360px, 768px and 1280px with Playwright screenshots, fix overflow and layout breakage. |
| `sup-experience-audit` | `wrk-visual-reviewer` | Start the app, capture Playwright screenshots of every sitemap page at 360px, 768px and 1280px into docs/quality/screenshots/, open each image with the Read tool and judge it against docs/product/design-spec.md: hierarchy, spacing, alignment, contrast, typography, consistency, polish. Fix visual defects in code, re-capture, and write docs/quality/visual-review.md with before and after notes per page. |

## DevOps & Release Manager (`mgr-release`)

Make the product shippable: CI pipeline, containerization, deployment configuration, observability, and documentation a new engineer can follow.

```mermaid
flowchart LR
    mgr_release["DevOps & Release Manager"]
    mgr_release --> sup_cicd["CI/CD Supervisor"]
    sup_cicd --> wrk_ci_pipeline(["CI Pipeline Engineer"])
    sup_cicd --> wrk_containerizer(["Containerization Engineer"])
    sup_cicd --> wrk_release_verifier(["Release Smoke Verifier"])
    mgr_release --> sup_deploy_docs["Deployment & Docs Supervisor"]
    sup_deploy_docs --> wrk_deploy_config(["Deployment Configurator"])
    sup_deploy_docs --> wrk_observability(["Observability Engineer"])
    sup_deploy_docs --> wrk_deployer(["Deployer"])
    sup_deploy_docs --> wrk_docs_writer(["Documentation Writer"])
    classDef mgr fill:#2563eb,color:#fff,stroke:#1e40af
    classDef sup fill:#7c3aed,color:#fff,stroke:#5b21b6
    classDef wrk fill:#ecfdf5,color:#064e3b,stroke:#059669
    class mgr_release mgr
    class sup_cicd,sup_deploy_docs sup
    class wrk_ci_pipeline,wrk_containerizer,wrk_release_verifier,wrk_deploy_config,wrk_observability,wrk_deployer,wrk_docs_writer wrk
```

| Supervisor | Worker | Task |
|---|---|---|
| `sup-cicd` | `wrk-ci-pipeline` | Write the CI workflow (GitHub Actions by default) that installs, lints, typechecks, tests and builds with caching. |
| `sup-cicd` | `wrk-containerizer` | Write a multi stage Dockerfile, .dockerignore and docker compose for local services, running as a non root user. |
| `sup-cicd` | `wrk-release-verifier` | Build the production artifact, start it, and run a scripted smoke test against health and key pages. Report exact results. |
| `sup-deploy-docs` | `wrk-deploy-config` | Write deployment configuration for the hosting target in stack.md and a step by step deploy guide in docs/release/deploy.md. |
| `sup-deploy-docs` | `wrk-observability` | Add a health endpoint, structured logging, error reporting hooks and basic request metrics. |
| `sup-deploy-docs` | `wrk-deployer` | Deploy the product to the hosting target in stack.md when its credentials are present in the environment (for example VERCEL_TOKEN, NETLIFY_AUTH_TOKEN, FLY_API_TOKEN, RENDER_API_KEY, EXPO_TOKEN for mobile builds), then verify the live URL with an HTTP check of the health endpoint and key pages. When credentials are absent, do not fake it: report the exact variables needed and the one command that will deploy once they are set. |
| `sup-deploy-docs` | `wrk-docs-writer` | Write README.md (overview, prerequisites, setup, run, test, deploy, structure) and verify each command by running it. |
