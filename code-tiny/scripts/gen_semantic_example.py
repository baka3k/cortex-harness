"""Generate semantic_output_example.json for the plans directory."""
import sys
import json

sys.path.insert(0, ".")

from tools.common.semantic_inference import SemanticInferenceEngine

engine = SemanticInferenceEngine()

functions = [
    {
        "symbol_id": "getUserProfile/3@services/user.ts",
        "name": "getUserProfile",
        "arity": 1,
        "code": "async function getUserProfile(userId: string): Promise<User> { return prisma.user.findUnique({ where: { id: userId } }); }",
        "comment": "",
        "return_type": "Promise<User>",
        "param_types": ["string"],
        "exported": True,
    },
    {
        "symbol_id": "isValidEmail/1@utils/validation.ts",
        "name": "isValidEmail",
        "arity": 1,
        "code": "function isValidEmail(email: string): boolean { return /^[^@]+@[^@]+\\.[^@]+$/.test(email); }",
        "comment": "",
        "return_type": "boolean",
        "param_types": ["string"],
        "exported": True,
    },
    {
        "symbol_id": "saveOrder/1@services/order.ts",
        "name": "saveOrder",
        "arity": 1,
        "code": "async function saveOrder(order: Order): Promise<void> { await db.orders.insert(order); }",
        "comment": "",
        "return_type": "Promise<void>",
        "param_types": ["Order"],
        "exported": True,
    },
    {
        "symbol_id": "calculateTax/2@utils/pricing.ts",
        "name": "calculateTax",
        "arity": 2,
        "code": "function calculateTax(amount: number, rate: number): number { return Math.round(amount * rate * 100) / 100; }",
        "comment": "",
        "return_type": "number",
        "param_types": ["number", "number"],
        "exported": False,
    },
    {
        "symbol_id": "deleteExpiredSessions/0@jobs/cleanup.ts",
        "name": "deleteExpiredSessions",
        "arity": 0,
        "code": "async function deleteExpiredSessions(): Promise<void> { await db.sessions.delete({ where: { expiresAt: { lt: new Date() } } }); }",
        "comment": "",
        "return_type": "Promise<void>",
        "param_types": [],
        "exported": False,
    },
    {
        "symbol_id": "buildAuthToken/2@auth/token.ts",
        "name": "buildAuthToken",
        "arity": 2,
        "code": 'function buildAuthToken(userId: string, roles: string[]): string { return jwt.sign({ userId, roles }, SECRET_KEY, { expiresIn: "24h" }); }',
        "comment": "",
        "return_type": "string",
        "param_types": ["string", "string[]"],
        "exported": True,
    },
    {
        "symbol_id": "handleFormSubmit/1@components/LoginForm.ts",
        "name": "handleFormSubmit",
        "arity": 1,
        "code": "async function handleFormSubmit(event: FormEvent): Promise<void> { event.preventDefault(); await login(formData); setState({ loading: false }); }",
        "comment": "",
        "return_type": "Promise<void>",
        "param_types": ["FormEvent"],
        "exported": False,
    },
    {
        "symbol_id": "parseJWTPayload/1@auth/token.ts",
        "name": "parseJWTPayload",
        "arity": 1,
        "code": "function parseJWTPayload(token: string): JWTPayload { return JSON.parse(atob(token.split('.')[1])); }",
        "comment": "",
        "return_type": "JWTPayload",
        "param_types": ["string"],
        "exported": False,
    },
]

calls = [
    {
        "caller_id": "getProfile_page/0@pages/profile.ts",
        "callee_name": "getUserProfile",
        "callee_id": "getUserProfile/3@services/user.ts",
        "callee_arity": 1,
    },
    {
        "caller_id": "signup_handler/0@routes/auth.ts",
        "callee_name": "isValidEmail",
        "callee_id": "isValidEmail/1@utils/validation.ts",
        "callee_arity": 1,
    },
    {
        "caller_id": "checkout_handler/0@routes/order.ts",
        "callee_name": "saveOrder",
        "callee_id": "saveOrder/1@services/order.ts",
        "callee_arity": 1,
    },
    {
        "caller_id": "checkout_handler/0@routes/order.ts",
        "callee_name": "calculateTax",
        "callee_id": "calculateTax/2@utils/pricing.ts",
        "callee_arity": 2,
    },
]

engine.enrich_corpus(functions, calls)

output = []
for f in functions:
    fid = f["symbol_id"]
    name_part, file_part = (fid.split("@") + [""])[:2]
    output.append({
        "symbol_id":   fid,
        "name":        f["name"],
        "file":        file_part or "",
        "summary":     f.get("summary", ""),
        "intent":      f.get("intent", "unknown"),
        "confidence":  f.get("doc_confidence", 0.0),
        "signals":     f.get("signals", {}),
        "inferred":    f.get("inferred_doc", True),
        "async":       "async" in (f.get("code") or ""),
        "side_effect": f.get("side_effect", False),
        "return_type": f.get("return_type", ""),
        "arity":       f["arity"],
        "exported":    f.get("exported", False),
        "note_preview": (f.get("note") or "")[:200],
    })

print(json.dumps(output, indent=2))
