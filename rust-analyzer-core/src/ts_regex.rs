//! Compiled regex patterns and factory maps for TypeScript analysis.
//!
//! Direct port of `tools/ts/utils/regex_patterns.py`. All ~40 patterns are
//! pre-compiled at first access via `once_cell::sync::Lazy`.

use once_cell::sync::Lazy;
use regex::Regex;
use std::collections::HashMap;

// ── 1. React role: screen classification ───────────────────────────────

pub const SCREEN_NAME_SUFFIXES: &[&str] = &["Screen", "Page", "View", "Tab", "Scene", "Activity"];

pub const WRAPPER_NAME_SUFFIXES: &[&str] = &[
    "Wrapper", "Layout", "Provider", "Shell", "Guard", "Boundary", "Container", "HOC", "Hoc", "Decorator",
];

pub static RE_HOC_FACTORY_NAME: Lazy<Regex> = Lazy::new(|| Regex::new(r"^with[A-Z]").unwrap());

pub static RE_WRAPS_CHILDREN: Lazy<Regex> = Lazy::new(|| Regex::new(r"\{\s*children\s*\}").unwrap());

pub const NAV_CHROME_SUFFIXES: &[&str] = &[
    "HeaderRight", "HeaderLeft", "HeaderTitle", "HeaderButton", "HeaderBackButton", "HeaderBackImage",
    "HeaderBar", "TabBar", "TabBarIcon", "TabIcon", "TabLabel", "TabBadge", "TabItem", "DrawerItem",
    "DrawerIcon", "DrawerLabel", "DrawerContent", "NavBar", "NavigationBar", "BottomTabBar", "Toolbar",
    "FooterBar", "StatusBar", "ActionBar",
];

pub const NAVIGATOR_NAME_SUFFIXES: &[&str] = &["Navigator", "Navigation", "Stack", "Router", "Switcher"];

pub static RE_NAVIGATOR_FACTORY_NAME: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^(?:create|make|build|setup)[A-Z].*(?:Navigator|Stack|Router|Navigation)\b").unwrap()
});

pub static RE_SCREEN_HOOKS: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)\b(?:useNavigation|useRoute|useNavigate|useHistory|useLocation|useParams|useNavigationState|useIsFocused|useFocusEffect|useScrollToTop|useRouter|usePathname|useSearchParams)\s*\(").unwrap()
});

pub static RE_SCREEN_NAV_CALL: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"\b(?:router|history|\w*(?:[Nn]avig\w*|[Nn]av[A-Z]\w*|[Nn]av))\s*\.\s*(?:navigate|push|goBack|replace|reset|pop|dispatch|redirect)\s*\(").unwrap()
});

pub static RE_SCREEN_PROP_NAMES: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"[({,]\s*(?:navigation|route)\s*[,)}\s:]").unwrap());

// ── 2. Middleware / backend-interaction detection ──────────────────────

pub static RE_MIDDLEWARE_API: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"(?i)\b(?:fetch|axios|got|ky|superagent|request|createApi|buildFetcher|XMLHttpRequest)\s*[\.(\"'`]"#).unwrap()
});

pub static RE_MIDDLEWARE_QUERY: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)\b(?:useQuery|useMutation|useInfiniteQuery|useSWR|useApolloQuery|useLazyQuery|gql|graphql|createAsyncThunk)\s*[\.(]").unwrap()
});

pub static RE_MIDDLEWARE_REDUX: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)\b(?:createSlice|createReducer|createAction|createStore|configureStore|applyMiddleware|useDispatch|useSelector)\s*[\.(]").unwrap()
});

pub static RE_SERVICE_LAYER: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)\b(?:prisma|knex|sequelize|mongoose|typeorm|redis|supabase|firebase|neo4j|mongodb|pg\.|mysql)\s*[\.(]").unwrap()
});

// ── 3. API call extraction ──────────────────────────────────────────────

pub static RE_FETCH_CALL: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"(?m)\bfetch\s*\(\s*(?P<url>[`'][^`']+[`']|"[^"]+"|`[^`]+`)"#).unwrap()
});

pub static RE_FETCH_METHOD: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"(?i)method\s*:\s*['"`](?P<method>GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)['"`]"#).unwrap()
});

pub static RE_AXIOS_SHORTHAND: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"(?im)\baxios\s*\.\s*(?P<method>get|post|put|patch|delete|head|options)\s*\(\s*(?P<url>[`'][^`']+[`']|"[^"]+"|`[^`]+`)"#).unwrap()
});

pub static RE_AXIOS_CONFIG: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"(?ms)\baxios\s*\(\s*\{[^}]*?url\s*:\s*(?P<url>[`'][^`']+[`']|"[^"]+"|`[^`]+`)[^}]*?(?:method\s*:\s*['"`](?P<method>[A-Z]+)['"`])?"#).unwrap()
});

pub static RE_HTTP_CLIENT: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"(?im)\bhttp\s*\.\s*(?P<method>get|post|put|patch|delete)\s*(?:<[^>]*>)?\s*\(\s*(?P<url>[`'][^`']+[`']|"[^"]+"|`[^`]+`)"#).unwrap()
});

pub static RE_NAMED_CLIENT: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"(?im)\b(?P<client>api|client|http|request|service|instance)\s*\.\s*(?P<method>get|post|put|patch|delete)\s*(?:<[^>]*>)?\s*\(\s*(?P<url>[`'][^`']+[`']|"[^"]+"|`[^`]+`)"#).unwrap()
});

pub static RE_AXIOS_CREATE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"(?m)\baxios\.create\s*\(\s*\{[^}]*?baseURL\s*:\s*(?P<base>[`'"][^`'"]*[`'"]|[`'"]+[^`'"]+[`'"]+)"#).unwrap()
});

pub static RE_ENV_VAR: Lazy<Regex> = Lazy::new(|| Regex::new(r"process\.env\.[A-Z_]+").unwrap());

// ── 4. Navigation call detection ───────────────────────────────────────

pub static RE_ASSIGN_USE_NAVIGATION: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?m)\bconst\s+(?P<var>[a-zA-Z_]\w+)\s*=\s*use\w*Navigation\s*\(").unwrap()
});

pub static RE_ASSIGN_USE_NAVIGATION_DESTRUCT: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?m)\bconst\s+\{[^}]{0,120}\bnavigate\b[^}]{0,120}\}\s*=\s*use\w*Navigation\s*\(").unwrap()
});

pub static RE_ASSIGN_USE_ROUTER: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?m)\bconst\s+(?P<var>[a-zA-Z_]\w+)\s*=\s*use\w*Router\s*\(").unwrap()
});

pub static RE_ASSIGN_USE_NAVIGATE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?m)\bconst\s+(?P<var>[a-zA-Z_]\w+)\s*=\s*use\w*Navigate\s*\(").unwrap()
});

pub static RE_ASSIGN_USE_HISTORY: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?m)\bconst\s+(?P<var>[a-zA-Z_]\w+)\s*=\s*useHistory\s*\(").unwrap()
});

pub static RE_NAV_PROP_CALL: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"(?m)\b(?:navigation|navigator)\s*(?:\.current\s*\??\s*)?\.(?P<method>navigate|push|replace|reset|goTo)\s*\(\s*['"`](?P<target>[A-Za-z0-9_./: -]+)['"`]"#).unwrap()
});

pub static RE_NAV_PROP_OBJ: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"(?m)\b(?:navigation|navigator)\s*\.(?:navigate|push|reset)\s*\(\s*\{\s*(?:pathname|name|routeName|screen)\s*:\s*['"`](?P<target>[A-Za-z0-9_./: -]+)['"`]"#).unwrap()
});

pub static RE_ROUTER_CALL: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"(?m)\b(?P<var>router|history)\s*\.(?P<method>navigate|push|replace|redirect)\s*\(\s*['"`](?P<target>[A-Za-z0-9_./: -]+)['"`]"#).unwrap()
});

pub static RE_ROUTER_OBJ: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"(?m)\b(?P<var>router|history)\s*\.(?P<method>navigate|push|replace)\s*\(\s*\{\s*pathname\s*:\s*['"`](?P<target>[A-Za-z0-9_./: -]+)['"`]"#).unwrap()
});

pub static RE_NAV_REF_CALL: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"(?m)\b\w*[Nn]av(?:igation)?[Rr]ef\b(?:[^;(]{0,40}\.current\s*\??\s*)?\s*\.\s*(?P<method>navigate|push|replace)\s*\(\s*['"`](?P<target>[A-Za-z0-9_./: -]+)['"`]"#).unwrap()
});

pub static RE_NAV_SERVICE_CALL: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"(?m)\b(?P<var>\w*(?:[Nn]avig\w*|[Nn]av[A-Z]\w*|[Nn]av))\s*(?:\.current\s*\??\s*)?\.\s*(?P<method>navigate|push|replace|reset|goTo)\s*\(\s*['"`](?P<target>[A-Za-z0-9_./: -]+)['"`]"#).unwrap()
});

pub static RE_NAV_SERVICE_OBJ: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"(?m)\b(?P<var>\w*(?:[Nn]avig\w*|[Nn]av[A-Z]\w*|[Nn]av))\s*\.\s*(?:navigate|push|reset)\s*\(\s*\{\s*(?:pathname|name|routeName|screen)\s*:\s*['"`](?P<target>[A-Za-z0-9_./: -]+)['"`]"#).unwrap()
});

pub static RE_JSX_LINK: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"(?ms)<(?:Link|NavLink)\b[^>]{0,300}?\b(?:href|to)\s*=\s*(?:['"](?P<route>/[^"'>{]+)['"]|\{\s*['"`](?P<route2>/[^"'`>{]+)['"`]\s*\})"#).unwrap()
});

pub static RE_JSX_NAVIGATE_EL: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"(?ms)<(?:Navigate|Redirect)\b[^>]{0,200}?\bto\s*=\s*(?:['"](?P<route>[^"'>{]+)['"]|\{\s*['"`](?P<route2>[^"'`>{]+)['"`]\s*\})"#).unwrap()
});

/// Build a per-variable regex: `var.navigate/push/replace/reset/goTo('Target')`.
pub fn nav_obj_method_re(var: &str) -> Regex {
    let escaped = regex::escape(var);
    Regex::new(&format!(
        r#"(?m)\b{escaped}\s*\.(?P<method>navigate|push|replace|reset|goTo)\s*\(\s*['"`](?P<target>[A-Za-z0-9_./: -]+)['"`]"#
    )).unwrap()
}

/// Build a per-variable regex: `var('/path')` or `var('Screen')`.
pub fn nav_fn_call_re(var: &str) -> Regex {
    let escaped = regex::escape(var);
    Regex::new(&format!(
        r#"(?m)\b{escaped}\s*\(\s*['"`](?P<target>[A-Za-z0-9_./: -]+)['"`]"#
    )).unwrap()
}

// ── 5. Navigation Intelligence V2.0 ─────────────────────────────────────

pub static RE_USER_TRIGGER: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?m)\b(?:onClick|onPress|onTap|onSubmit|onConfirm|onLongPress|handlePress|handleClick|handleSubmit|handleTap|onSelectItem)\b").unwrap()
});

pub static RE_SYSTEM_TRIGGER: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?m)\b(?:useEffect|componentDidMount|componentDidUpdate|useLayoutEffect|useMemo|useCallback)\s*\(").unwrap()
});

pub static RE_ASYNC_TRIGGER: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?m)(?:\.then\s*\(|await\s+\w|\.\s*catch\s*\()").unwrap());

pub static RE_AUTH_GUARD: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?m)\b(?:isAuth(?:enticated)?|isLoggedIn|token\b|user\.id|requiresAuth|userLoggedIn)\b").unwrap()
});

pub static RE_PERM_GUARD: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?m)\b(?:hasPermission|canAccess|role\s*===|isAdmin|isOwner|checkPermission)\b").unwrap()
});

pub static RE_SCREEN_ELEM_START: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?m)<(?:\w+\.)?Screen\b").unwrap());

pub static RE_SCREEN_NAME_ATTR: Lazy<Regex> =
    Lazy::new(|| Regex::new(r#"\bname\s*=\s*['"](?P<name>[^'"]{1,80})['"]"#).unwrap());

pub static RE_SCREEN_COMP_ATTR: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"\bcomponent\s*=\s*\{(?P<comp>\w+)\}").unwrap());

// ── 6. Navigator factory + ParamList detection ─────────────────────────

pub static RE_NAVIGATOR_FACTORY: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?m)(?:const|let|var)\s+(?P<var_name>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?P<factory>create(?:Stack|BottomTab|Drawer|NativeStack|MaterialTopTab)Navigator)(?:<\s*(?P<generic>[A-Za-z_$][A-Za-z0-9_$<>, ]*?)\s*>)?\s*\(\s*\)").unwrap()
});

/// Factory name → nav type map.
pub fn factory_to_nav_type(factory: &str) -> &'static str {
    match factory {
        "createStackNavigator" => "stack",
        "createNativeStackNavigator" => "native_stack",
        "createBottomTabNavigator" => "tab",
        "createDrawerNavigator" => "drawer",
        "createMaterialTopTabNavigator" => "material_top",
        _ => "unknown",
    }
}

// ── 7. Function-kind map for call_expression initializers ──────────────

pub static CALL_EXPR_KIND_MAP: Lazy<HashMap<&'static str, &'static str>> = Lazy::new(|| {
    let mut m = HashMap::new();
    // ── Redux Toolkit ──
    m.insert("createAsyncThunk", "thunk");
    m.insert("createSlice", "redux_slice");
    m.insert("createAction", "action_creator");
    m.insert("createReducer", "reducer");
    m.insert("createSelector", "selector");
    m.insert("createApi", "api_service");
    m.insert("createEntityAdapter", "entity_adapter");
    m.insert("createListenerMiddleware", "middleware");
    // ── React wrappers / HOCs ──
    m.insert("memo", "component");
    m.insert("forwardRef", "component");
    m.insert("lazy", "component");
    m.insert("connect", "hoc_connected");
    m.insert("compose", "hoc_composed");
    m.insert("pipe", "hoc_composed");
    m.insert("createContext", "context");
    m.insert("styled", "styled_component");
    // ── Vue / Nuxt ──
    m.insert("defineComponent", "component");
    m.insert("defineAsyncComponent", "component");
    m.insert("defineCustomElement", "component");
    m.insert("defineStore", "store");
    m.insert("defineNuxtConfig", "config");
    m.insert("defineNuxtPlugin", "plugin");
    m.insert("defineNuxtRouteMiddleware", "middleware");
    m.insert("defineEventHandler", "handler");
    m.insert("definePage", "page");
    m.insert("definePageMeta", "page_meta");
    // ── State management (non-Redux) ──
    m.insert("createStore", "store");
    m.insert("atom", "atom");
    m.insert("createMachine", "state_machine");
    m.insert("createModel", "model");
    m.insert("makeAutoObservable", "observable");
    m.insert("observable", "observable");
    // ── Server / routing / middleware ──
    m.insert("createServer", "server");
    m.insert("createApp", "app");
    m.insert("createRouter", "router");
    m.insert("createTRPCRouter", "router");
    m.insert("createCallerFactory", "factory");
    m.insert("createMiddleware", "middleware");
    m.insert("createClient", "client");
    m.insert("createTRPCProxyClient", "client");
    m.insert("initTRPC", "trpc_init");
    m.insert("initTRPC.create", "trpc_init");
    // ── Configuration / build ──
    m.insert("defineConfig", "config");
    // ── Styling ──
    m.insert("makeStyles", "styles");
    m.insert("createStyles", "styles");
    m.insert("createTheme", "theme");
    // ── Testing ──
    m.insert("createMock", "mock");
    m.insert("createStub", "mock");
    // ── Angular ──
    m.insert("inject", "injection");
    // ── Generic ──
    m.insert("create", "function_variable");
    m
});

// ── Directory classification (ported from file_utils.py) ───────────────

pub const SCREEN_DIR_SEGMENTS: &[&str] = &["screens", "screen", "pages", "page", "views", "routes", "route"];

pub const SERVICE_DIR_SEGMENTS: &[&str] = &[
    "api", "apis", "services", "service", "middleware", "http", "network", "repository", "repositories",
];

pub const INDEX_BASENAMES: &[&str] = &["index.ts", "index.tsx", "index.js", "index.jsx"];

pub fn is_screen_file(file_path: &str) -> bool {
    let normalized = file_path.replace('\\', "/");
    normalized.split('/').any(|seg| SCREEN_DIR_SEGMENTS.contains(&seg.to_ascii_lowercase().as_str()))
}

pub fn is_service_file(file_path: &str) -> bool {
    let normalized = file_path.replace('\\', "/");
    normalized.split('/').any(|seg| SERVICE_DIR_SEGMENTS.contains(&seg.to_ascii_lowercase().as_str()))
}

pub fn index_module_name(file_path: &str) -> Option<String> {
    let normalized = file_path.replace('\\', "/");
    let parts: Vec<&str> = normalized.split('/').collect();
    if parts.len() >= 2 && INDEX_BASENAMES.contains(&parts[parts.len() - 1]) {
        Some(parts[parts.len() - 2].to_string())
    } else {
        None
    }
}

// ── URL helpers (ported from symbol_agent.py) ──────────────────────────

pub fn normalize_url_pattern(url: &str) -> String {
    if url.is_empty() {
        return String::new();
    }
    let mut url = url.trim().to_string();
    let re = Regex::new(r"\$\{[^}]+\}").unwrap();
    url = re.replace_all(&url, ":param").to_string();
    if url != "/" && url.ends_with('/') {
        url = url.trim_end_matches('/').to_string();
    }
    url
}

pub fn normalize_http_method(method: &str) -> String {
    method.to_uppercase()
}

pub fn merge_base_url(base: Option<&str>, path: &str) -> String {
    let base = match base {
        Some(b) if !b.is_empty() => b,
        _ => return normalize_url_pattern(path),
    };
    let base = base.trim_end_matches('/');
    let path = if path.starts_with('/') { path.to_string() } else { format!("/{}", path) };
    normalize_url_pattern(&format!("{}{}", base, path))
}

pub fn clean_url_expr(raw: &str) -> String {
    let mut s = raw.trim().trim_matches(|c| c == '`' || c == '\'' || c == '"').to_string();
    s = RE_ENV_VAR.replace_all(&s, "").to_string();
    s = s.trim().trim_start_matches('+').trim().to_string();
    s = s.trim_matches(|c| c == '"' || c == '\'' || c == '`').trim().to_string();
    s
}

pub fn extract_file_base_url(code: &str) -> String {
    let m = match RE_AXIOS_CREATE.captures(code) {
        Some(m) => m,
        None => return String::new(),
    };
    let raw = m.name("base").map(|m| m.as_str()).unwrap_or("").trim().trim_matches(|c| c == '`' || c == '\'' || c == '"');
    let cleaned = RE_ENV_VAR.replace_all(raw, "").trim().trim_start_matches('+').trim().to_string();
    let cleaned = cleaned.trim_matches(|c| c == '`' || c == '\'' || c == '"').trim().to_string();
    if cleaned.is_empty() || cleaned.contains("process") || cleaned.to_lowercase().contains("env") {
        return String::new();
    }
    normalize_url_pattern(&cleaned)
}
