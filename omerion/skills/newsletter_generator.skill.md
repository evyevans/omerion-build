---
name: "Newsletter Generator"
version: "1.0.0"
runtime: "langgraph"
trigger: "scheduler"  # Driven by dedicated mode-aware jobs in runtime/scheduler.py
                      # (newsletter-weekly / newsletter-skillpack / newsletter-playbook).
                      # NO `schedule:` here on purpose — the generic frontmatter loop
                      # would register a 4th, mode-less job (defaulting to skillpack).
---

# Newsletter Generator

Generates hyper-tailored content (skill packs every 2 weeks, playbooks every 30 days) per industry and sends them to active newsletter subscribers.
