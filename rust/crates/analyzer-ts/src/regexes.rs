//! Port `tools/ts/utils/regex_patterns.py` — compiled patterns cho React
//! role / middleware / API call / navigation / navigator factory.

use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};

use regex::Regex;

fn re(pattern: &str) -> Regex {
    Regex::new(pattern).expect("ts analyzer regex")
}

// ── 1. React role ───────────────────────────────────────────────────────────

#[allow(dead_code)]
pub const WRAPPER_NAME_SUFFIXES: &[&str] = &[
    "Wrapper", "Layout", "Provider", "Shell", "Guard", "Boundary", "Container", "HOC", "Hoc",
    "Decorator",
];

#[allow(dead_code)]
pub const NAV_CHROME_SUFFIXES: &[&str] = &[
    "HeaderRight",
    "HeaderLeft",
    "HeaderTitle",
    "HeaderButton",
    "HeaderBackButton",
    "HeaderBackImage",
    "HeaderBar",
    "TabBar",
    "TabBarIcon",
    "TabIcon",
    "TabLabel",
    "TabBadge",
    "TabItem",
    "DrawerItem",
    "DrawerIcon",
    "DrawerLabel",
    "DrawerContent",
    "NavBar",
    "NavigationBar",
    "BottomTabBar",
    "Toolbar",
    "FooterBar",
    "StatusBar",
    "ActionBar",
];

#[allow(dead_code)]
pub const NAVIGATOR_NAME_SUFFIXES: &[&str] = &["Navigator", "Navigation", "Stack", "Router", "Switcher"];

pub struct TsRegexes {
    pub hoc_factory_name: Regex,          // ^with[A-Z]
    pub wraps_children: Regex,            // (?m){\s*children\s*}
    pub navigator_factory_name: Regex,
    pub screen_hooks: Regex,
    pub screen_nav_call: Regex,
    pub screen_prop_names: Regex,
    pub middleware_api: Regex,
    pub middleware_query: Regex,
    pub middleware_redux: Regex,
    pub service_layer: Regex,
    pub fetch_call: Regex,
    pub fetch_method: Regex,
    pub axios_shorthand: Regex,
    pub axios_config: Regex,
    pub http_client: Regex,
    pub named_client: Regex,
    pub axios_create: Regex,
    pub env_var: Regex,
    pub assign_use_navigation: Regex,
    pub assign_use_navigation_destruct: Regex,
    pub assign_use_router: Regex,
    pub assign_use_navigate: Regex,
    pub assign_use_history: Regex,
    pub nav_prop_call: Regex,
    pub nav_prop_obj: Regex,
    pub router_call: Regex,
    pub router_obj: Regex,
    pub nav_ref_call: Regex,
    pub nav_service_call: Regex,
    pub nav_service_obj: Regex,
    pub jsx_link: Regex,
    pub jsx_navigate_el: Regex,
    pub has_use_router: Regex,
    pub has_use_history: Regex,
    pub user_trigger: Regex,
    pub system_trigger: Regex,
    pub async_trigger: Regex,
    pub auth_guard: Regex,
    pub perm_guard: Regex,
    pub screen_elem_start: Regex,
    pub screen_name_attr: Regex,
    pub screen_comp_attr: Regex,
    pub navigator_factory: Regex,
    pub call_expr_kind_map: HashMap<&'static str, &'static str>,
}

pub fn regexes() -> &'static TsRegexes {
    static RE: OnceLock<TsRegexes> = OnceLock::new();
    RE.get_or_init(|| TsRegexes {
        hoc_factory_name: re(r"^with[A-Z]"),
        wraps_children: re(r"(?m)\{\s*children\s*\}"),
        navigator_factory_name: re(
            r"^(?:create|make|build|setup)[A-Z].*(?:Navigator|Stack|Router|Navigation)\b",
        ),
        screen_hooks: re(
            r"\b(?:useNavigation|useRoute|useNavigate|useHistory|useLocation|useParams|useNavigationState|useIsFocused|useFocusEffect|useScrollToTop|useRouter|usePathname|useSearchParams)\s*\(",
        ),
        screen_nav_call: re(
            r"\b(?:router|history|\w*(?:[Nn]avig\w*|[Nn]av[A-Z]\w*|[Nn]av))\s*\.\s*(?:navigate|push|goBack|replace|reset|pop|dispatch|redirect)\s*\(",
        ),
        screen_prop_names: re(r"[({,]\s*(?:navigation|route)\s*[,)}\s:]"),
        middleware_api: re(
            r#"(?i)\b(?:fetch|axios|got|ky|superagent|request|createApi|buildFetcher|XMLHttpRequest)\s*[.("'`]"#,
        ),
        middleware_query: re(
            r"(?i)\b(?:useQuery|useMutation|useInfiniteQuery|useSWR|useApolloQuery|useLazyQuery|gql|graphql|createAsyncThunk)\s*[.(]",
        ),
        middleware_redux: re(
            r"(?i)\b(?:createSlice|createReducer|createAction|createStore|configureStore|applyMiddleware|useDispatch|useSelector)\s*[.(]",
        ),
        service_layer: re(
            r"(?i)\b(?:prisma|knex|sequelize|mongoose|typeorm|redis|supabase|firebase|neo4j|mongodb|pg\.|mysql)\s*[.(]",
        ),
        fetch_call: re(
            r#"(?m)\bfetch\s*\(\s*(?P<url>[`'][^`']+[`']|"[^"]+"|`[^`]+`)"#,
        ),
        fetch_method: re(
            r#"(?i)method\s*:\s*['"`](?P<method>GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)['"`]"#,
        ),
        axios_shorthand: re(
            r#"(?im)\baxios\s*\.\s*(?P<method>get|post|put|patch|delete|head|options)\s*\(\s*(?P<url>[`'][^`']+[`']|"[^"]+"|[`][^`]+[`])"#,
        ),
        axios_config: re(
            r#"(?ims)\baxios\s*\(\s*\{[^}]*?url\s*:\s*(?P<url>[`'][^`']+[`']|"[^"]+"|`[^`]+`)[^}]*?(?:method\s*:\s*['"`](?P<method>[A-Z]+)['"`])?"#,
        ),
        http_client: re(
            r#"(?im)\bhttp\s*\.\s*(?P<method>get|post|put|patch|delete)\s*(?:<[^>]*>)?\s*\(\s*(?P<url>[`'][^`']+[`']|"[^"]+"|`[^`]+`)"#,
        ),
        named_client: re(
            r#"(?im)\b(?P<client>api|client|http|request|service|instance)\s*\.\s*(?P<method>get|post|put|patch|delete)\s*(?:<[^>]*>)?\s*\(\s*(?P<url>[`'][^`']+[`']|"[^"]+"|`[^`]+`)"#,
        ),
        axios_create: re(
            r#"(?m)\baxios\.create\s*\(\s*\{[^}]*?baseURL\s*:\s*(?P<base>[`'"][^`'"]*[`'"]|[`'"]+[^`'"]+[`'"]+)"#,
        ),
        env_var: re(r"process\.env\.[A-Z_]+"),
        assign_use_navigation: re(r"(?m)\bconst\s+(?P<var>[a-zA-Z_]\w+)\s*=\s*use\w*Navigation\s*\("),
        assign_use_navigation_destruct: re(
            r"(?m)\bconst\s+\{[^}]{0,120}\bnavigate\b[^}]{0,120}\}\s*=\s*use\w*Navigation\s*\(",
        ),
        assign_use_router: re(r"(?m)\bconst\s+(?P<var>[a-zA-Z_]\w+)\s*=\s*use\w*Router\s*\("),
        assign_use_navigate: re(r"(?m)\bconst\s+(?P<var>[a-zA-Z_]\w+)\s*=\s*use\w*Navigate\s*\("),
        assign_use_history: re(r"(?m)\bconst\s+(?P<var>[a-zA-Z_]\w+)\s*=\s*useHistory\s*\("),
        nav_prop_call: re(
            r#"(?m)\b(?:navigation|navigator)\s*(?:\.current\s*\??\s*)?\.(?P<method>navigate|push|replace|reset|goTo)\s*\(\s*['"`](?P<target>[A-Za-z0-9_./: -]+)['"`]"#,
        ),
        nav_prop_obj: re(
            r#"(?m)\b(?:navigation|navigator)\s*\.(?:navigate|push|reset)\s*\(\s*\{\s*(?:pathname|name|routeName|screen)\s*:\s*['"`](?P<target>[A-Za-z0-9_./: -]+)['"`]"#,
        ),
        router_call: re(
            r#"(?m)\b(?P<var>router|history)\s*\.(?P<method>navigate|push|replace|redirect)\s*\(\s*['"`](?P<target>[A-Za-z0-9_./: -]+)['"`]"#,
        ),
        router_obj: re(
            r#"(?m)\b(?P<var>router|history)\s*\.(?P<method>navigate|push|replace)\s*\(\s*\{\s*pathname\s*:\s*['"`](?P<target>[A-Za-z0-9_./: -]+)['"`]"#,
        ),
        nav_ref_call: re(
            r#"(?m)\b\w*[Nn]av(?:igation)?[Rr]ef\b(?:[^;(]{0,40}\.current\s*\??\s*)?\s*\.\s*(?P<method>navigate|push|replace)\s*\(\s*['"`](?P<target>[A-Za-z0-9_./: -]+)['"`]"#,
        ),
        nav_service_call: re(
            r#"(?m)\b(?P<var>\w*(?:[Nn]avig\w*|[Nn]av[A-Z]\w*|[Nn]av))\s*(?:\.current\s*\??\s*)?\.\s*(?P<method>navigate|push|replace|reset|goTo)\s*\(\s*['"`](?P<target>[A-Za-z0-9_./: -]+)['"`]"#,
        ),
        nav_service_obj: re(
            r#"(?m)\b(?P<var>\w*(?:[Nn]avig\w*|[Nn]av[A-Z]\w*|[Nn]av))\s*\.\s*(?:navigate|push|reset)\s*\(\s*\{\s*(?:pathname|name|routeName|screen)\s*:\s*['"`](?P<target>[A-Za-z0-9_./: -]+)['"`]"#,
        ),
        jsx_link: re(
            r#"(?ms)<(?:Link|NavLink)\b[^>]{0,300}?\b(?:href|to)\s*=\s*(?:['"](?P<route>/[^"'>{]+)['"]|\{\s*['"`](?P<route2>/[^"'`>{]+)['"`]\s*\})"#,
        ),
        jsx_navigate_el: re(
            r#"(?ms)<(?:Navigate|Redirect)\b[^>]{0,200}?\bto\s*=\s*(?:['"](?P<route>[^"'>{]+)['"]|\{\s*['"`](?P<route2>[^"'`>{]+)['"`]\s*\})"#,
        ),
        has_use_router: re(r"\buseRouter\s*\("),
        has_use_history: re(r"\buseHistory\s*\("),
        user_trigger: re(
            r"(?m)\b(?:onClick|onPress|onTap|onSubmit|onConfirm|onLongPress|handlePress|handleClick|handleSubmit|handleTap|onSelectItem)\b",
        ),
        system_trigger: re(
            r"(?m)\b(?:useEffect|componentDidMount|componentDidUpdate|useLayoutEffect|useMemo|useCallback)\s*\(",
        ),
        async_trigger: re(r"(?m)(?:\.then\s*\(|await\s+\w|\.\s*catch\s*\()"),
        auth_guard: re(
            r"(?m)\b(?:isAuth(?:enticated)?|isLoggedIn|token\b|user\.id|requiresAuth|userLoggedIn)\b",
        ),
        perm_guard: re(
            r"(?m)\b(?:hasPermission|canAccess|role\s*===|isAdmin|isOwner|checkPermission)\b",
        ),
        screen_elem_start: re(r"(?m)<(?:\w+\.)?Screen\b"),
        screen_name_attr: re(r#"\bname\s*=\s*['"](?P<name>[^'"]{1,80})['"]"#),
        screen_comp_attr: re(r"\bcomponent\s*=\s*\{(?P<comp>\w+)\}"),
        navigator_factory: re(
            r"(?m)(?:const|let|var)\s+(?P<var_name>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?P<factory>create(?:Stack|BottomTab|Drawer|NativeStack|MaterialTopTab)Navigator)(?:<\s*(?P<generic>[A-Za-z_$][A-Za-z0-9_$<>, ]*?)\s*>)?\s*\(\s*\)",
        ),
        call_expr_kind_map: HashMap::from([
            // Redux Toolkit
            ("createAsyncThunk", "thunk"),
            ("createSlice", "redux_slice"),
            ("createAction", "action_creator"),
            ("createReducer", "reducer"),
            ("createSelector", "selector"),
            ("createApi", "api_service"),
            ("createEntityAdapter", "entity_adapter"),
            ("createListenerMiddleware", "middleware"),
            // React wrappers / HOCs
            ("memo", "component"),
            ("forwardRef", "component"),
            ("lazy", "component"),
            ("connect", "hoc_connected"),
            ("compose", "hoc_composed"),
            ("pipe", "hoc_composed"),
            ("createContext", "context"),
            ("styled", "styled_component"),
            // Vue / Nuxt
            ("defineComponent", "component"),
            ("defineAsyncComponent", "component"),
            ("defineCustomElement", "component"),
            ("defineStore", "store"),
            ("defineNuxtConfig", "config"),
            ("defineNuxtPlugin", "plugin"),
            ("defineNuxtRouteMiddleware", "middleware"),
            ("defineEventHandler", "handler"),
            ("definePage", "page"),
            ("definePageMeta", "page_meta"),
            // State management (non-Redux)
            ("createStore", "store"),
            ("atom", "atom"),
            ("createMachine", "state_machine"),
            ("createModel", "model"),
            ("makeAutoObservable", "observable"),
            ("observable", "observable"),
            // Server / routing / middleware
            ("createServer", "server"),
            ("createApp", "app"),
            ("createRouter", "router"),
            ("createTRPCRouter", "router"),
            ("createCallerFactory", "factory"),
            ("createMiddleware", "middleware"),
            ("createClient", "client"),
            ("createTRPCProxyClient", "client"),
            ("initTRPC", "trpc_init"),
            ("initTRPC.create", "trpc_init"),
            // Configuration / build
            ("defineConfig", "config"),
            // Styling
            ("makeStyles", "styles"),
            ("createStyles", "styles"),
            ("createTheme", "theme"),
            // Testing
            ("createMock", "mock"),
            ("createStub", "mock"),
            // Angular
            ("inject", "injection"),
            // Generic
            ("create", "function_variable"),
        ]),
    })
}

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

/// `_nav_obj_method_re(var)` — cached per var (lru_cache 64 phía Python).
pub fn nav_obj_method_re(var: &str) -> Regex {
    static CACHE: OnceLock<Mutex<HashMap<String, Regex>>> = OnceLock::new();
    let cache = CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    let mut guard = cache.lock().unwrap();
    guard
        .entry(var.to_string())
        .or_insert_with(|| {
            re(&format!(
                r#"\b{}\s*\.(?P<method>navigate|push|replace|reset|goTo)\s*\(\s*['"`](?P<target>[A-Za-z0-9_./: -]+)['"`]"#,
                regex::escape(var)
            ))
        })
        .clone()
}

/// `_nav_fn_call_re(var)` — cached per var.
pub fn nav_fn_call_re(var: &str) -> Regex {
    static CACHE: OnceLock<Mutex<HashMap<String, Regex>>> = OnceLock::new();
    let cache = CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    let mut guard = cache.lock().unwrap();
    guard
        .entry(var.to_string())
        .or_insert_with(|| {
            re(&format!(
                r#"\b{}\s*\(\s*['"`](?P<target>[A-Za-z0-9_./: -]+)['"`]"#,
                regex::escape(var)
            ))
        })
        .clone()
}

/// Python `str.endswith(tuple)` trên nhiều suffix.
pub fn ends_with_any(name: &str, suffixes: &[&str]) -> bool {
    suffixes.iter().any(|suffix| name.ends_with(suffix))
}
