tôi muốn hoàn thiện bộ cli dể mọi người chỉ dùng cli thôi, đầu tiên hãy hoàn thiện cli init, 

1. Gen ra config file
dev init sẽ tạo ra file json đặt tại thư mục của dự án target - đường dẫn của file sẽ là .cortext-harness/config/dev.json hoặc .cortext-harness/config/prod.json, config có nhiều file khác nhau cho các môi trường phát triển khác nhau
và cần có status chỉ rõ config nào đang được active (quy định bằng trường active trong file config)

2. Gen ra cấu trúc project
```
.
├── docs/                        # Project Documentation
│   ├── design-docs/             # System Architecture & Design
│   │   ├── index.md
│   │   ├── core-beliefs.md
│   │   └── ...
│   ├── exec-plans/              # Execution & Tracking
│   │   ├── active/              # Ongoing sprints/tasks
│   │   ├── completed/           # Archive of finished plans
│   │   └── tech-debt-tracker.md # Legacy issues & modernization gaps
│   ├── generated/               # Auto-generated Assets
│   │   └── db-schema.md         # Schema diagrams (Neo4j/SQL)
│   ├── product-specs/           # Functional Requirements
│   │   ├── index.md
│   │   ├── new-user-onboarding.md
│   │   └── ...
│   ├── references/              # LLM Context & Standards
│   │   ├── design-system-reference-llms.txt
│   │   ├── nixpacks-llms.txt
│   │   ├── uv-llms.txt
│   │   └── ...
│   ├── DESIGN.md                # High-level Design Overview
│   ├── FRONTEND.md              # Frontend Guidelines
│   ├── PLANS.md                 # Project Roadmap
│   ├── PRODUCT_SENSE.md         # Product Logic & Philosophy
│   ├── QUALITY_SCORE.md         # Engineering Standards
│   ├── RELIABILITY.md           # Stability & Error Handling
│   └── SECURITY.md              # Security Protocols
│
├── src/                         # Source Code (Parallel to docs)
│   ├── core/                    # Business Logic
│   │   ├── migration/           # Legacy-to-Modernization engine
│   │   └── services/            # Main application services
│   ├── infra/                   # Infrastructure & Data
│   │   ├── persistence/         # Database drivers (Neo4j, Qdrant..etc)
│   │   └── providers/           # External AI/LLM API wrappers
│   ├── interface/               # Entry Points
│   │   ├── api/                 # REST/gRPC endpoints
│   │   └── cli/                 # Terminal tools (e.g., "Knows" project)
│   └── shared/                  # Common Utilities & Types
├── AGENTS.md
├── ARCHITECTURE.md
├── .cursorrules                 # AI Instruction Set
└── README.md                    # Project Onboarding
 ```
 Cấu trúc folder này sẽ được phản ánh vào file config
 ví dụ: 

 "source": {
      "git": "",
      "folder": [
        "docs"
      ]
    }
hay 
"source": {
      "git": "",
      "folder": [
        "src"
      ]
    }