# SimplifyNext-AgenticAI

RouteProof

One-line mission: RouteProof verifies whether an approved accessible campus route is clear now, guides a student along it, and adapts when the physical environment changes.

Project description

A wheelchair-using student travelling to class needs to know that the designated step-free route is clear at the moment they need it, because a route that is accessible on a static map can still be blocked by a delivery cart, locked door, temporary barrier, or construction. Discovering that obstruction only after reaching it can mean backtracking, arriving late, or being left without a safe alternative.

RouteProof is a bounded Physical AI agent that turns accessibility from a static map into a live, evidence-backed service. A student or accessibility team provides a destination. RouteProof selects from pre-approved accessible routes and gives the chosen route instruction to NaVILA, which converts current and historical camera observations into navigation actions for a simulated Unitree robot. After each observation, RouteProof can allow navigation to continue, trigger a zero-velocity safety stop, advance to another approved route, or declare that no safe route remains.

The visible agentic loop is:

Plan: select an approved accessible route to the requested destination.

Act: use NaVILA and OrcaLab to navigate the route.

Observe: inspect the latest camera frame or receive a simulated obstruction event.

Adapt: stop and issue a new route instruction when the selected route is blocked.

Escalate: if all approved routes are unavailable, save visual evidence and create a facilities ticket with the obstruction, attempted routes, and robot position.

This differs from a conventional accessibility map because RouteProof responds to temporary conditions. It also differs from obstacle avoidance alone: avoiding a cart prevents collision, but it does not tell a wheelchair user whether the intended route is still usable, select an approved detour, or notify the team responsible for removing the obstruction. RouteProof adds that mission-level reasoning and operational follow-through while keeping safety-critical stopping deterministic.

The current proof of concept is implemented in Python around the existing NaVILA NavigationRunner. It supports pre-approved route plans, live navigation instructions, an interruptible safety guard, position-preserving rerouting, evidence capture, and local facilities-ticket receipts. The reliable demonstration uses a manually triggered temporary-blockage event to validate the complete stop-reroute-escalate workflow. A camera obstruction detector is implemented as a replaceable component and covered by unit tests, but RGB-only perception is not presented as an exact wheelchair-clearance measurement.

The prototype will be evaluated through three scenarios: a clear route, a blocked primary route with an available alternative, and all approved routes blocked. Relevant measures are task success rate, correct reroute rate, collision and emergency-stop record, human intervention rate, cycle time, evidence completeness, and robustness under changed clutter or starting position. These directly follow the hackathon's recommended Physical AI evaluation approach.

RouteProof benefits students by reducing the chance of discovering an unusable route mid-journey. Accessibility teams gain a current route-status signal, while facilities teams receive actionable evidence instead of a vague report. A pilot can begin with one destination and two approved routes, then expand into a campus route graph. Live RGB and motion-progress fusion is the next development step. Calibrated depth or LiDAR clearance sensing, real Unitree Go2 deployment, and facilities-system integration remain future work.

Judging-criteria alignment

Benefits delivered: replaces uncertainty at departure time with a verified route outcome and gives facilities teams evidence they can act on.

Originality: adds mission-level accessibility reasoning and operational follow-through above a navigation model; obstacle avoidance protects the robot, while RouteProof protects the journey.

Effectiveness: demonstrates three bounded outcomes—clear, blocked with an approved detour, and no safe route—with explicit acceptance targets.

Technical quality: separates NaVILA reasoning from a deterministic zero-velocity guard, approved-route allow-list, finite retries, position-preserving rerouting, and structured evidence.

Presentation: makes the decision visible in a short simulation: navigate, trigger a temporary obstruction, stop, reroute, then verify or issue a ticket.

Evidence and scope

The World Health Organization estimates that 1.3 billion people, or 16% of the global population, experience significant disability: https://www.who.int/news-room/fact-sheets/detail/disability-and-health

Singapore's Building and Construction Authority describes its accessibility code as the baseline for accessible buildings and inclusive use: https://www1.bca.gov.sg/safety-and-standards/accessibility/code-on-accessibility-in-the-built-environment/

NaVILA is a two-level vision-language-action and locomotion framework: https://navila-bot.github.io/static/navila_paper.pdf

Team name: AAABY

Members: Adam Prawira Prabowo, Chin Jia Hang, Woo Yu Xin, Aurelius Sin, Tan Cheng Loong Beny

Repository URL: https://github.com/augenst3rnnn/SimplifyNext-AgenticAI