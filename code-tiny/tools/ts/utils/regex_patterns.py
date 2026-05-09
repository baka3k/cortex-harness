"""Compiled regex patterns and name suffix tuples for TypeScript analysis.

All ``_RE_*`` constants are defined here so that they can be imported by
both the agent modules and by ts_analyzer.py (which re-exports them for
backward compatibility with ts_backend_analyzer.py).

Grouping follows their primary usage domain:
  1. React role classification (screen vs component vs hook)
  2. Middleware / backend-interaction detection
  3. API call extraction (fetch / axios / HttpClient)
  4. Navigation call detection (useNavigation, navigate(), Link, etc.)
  5. Navigation Intelligence V2.0 (trigger type, guard, route config)
  6. Navigator factory + ParamList detection
"""
from __future__ import annotations

import re
import functools
from typing import Dict, Tuple


# ─── 1. React role: screen classification ────────────────────────────────────

# Name suffixes that mark a component as a Screen (weak signal — used only as fallback)
_SCREEN_NAME_SUFFIXES: Tuple[str, ...] = ("Screen", "Page", "View", "Tab", "Scene", "Activity")

# HOC / layout-wrapper name suffixes — these are NEVER screens
_WRAPPER_NAME_SUFFIXES: Tuple[str, ...] = (
    "Wrapper", "Layout", "Provider", "Shell", "Guard",
    "Boundary", "Container", "HOC", "Hoc", "Decorator",
)

# Detect HOC-factory naming: withAuth, withNavigation, withTheme …
_RE_HOC_FACTORY_NAME = re.compile(r'^with[A-Z]')

# Detect layout components that render {children} in JSX
_RE_WRAPS_CHILDREN = re.compile(r'\{\s*children\s*\}', re.MULTILINE)

# Navigation UI chrome — small widgets that live inside a navigator's header/tabs/
# drawer but are NOT route-level screens.
_NAV_CHROME_SUFFIXES: Tuple[str, ...] = (
    "HeaderRight", "HeaderLeft", "HeaderTitle", "HeaderButton",
    "HeaderBackButton", "HeaderBackImage", "HeaderBar",
    "TabBar", "TabBarIcon", "TabIcon", "TabLabel", "TabBadge", "TabItem",
    "DrawerItem", "DrawerIcon", "DrawerLabel", "DrawerContent",
    "NavBar", "NavigationBar", "BottomTabBar", "Toolbar",
    "FooterBar", "StatusBar", "ActionBar",
)

# Navigator / router components — navigation INFRASTRUCTURE, not route-level screens
_NAVIGATOR_NAME_SUFFIXES: Tuple[str, ...] = (
    "Navigator", "Navigation",
    "Stack", "Router",
    "Switcher",
)
_RE_NAVIGATOR_FACTORY_NAME = re.compile(
    r'^(?:create|make|build|setup)[A-Z].*(?:Navigator|Stack|Router|Navigation)\b'
)

# Strong screen signals: navigation hooks used inside the function body
_RE_SCREEN_HOOKS = re.compile(
    r"\b(?:useNavigation|useRoute|useNavigate|useHistory|useLocation|useParams|"
    r"useNavigationState|useIsFocused|useFocusEffect|useScrollToTop|"
    r"useRouter|usePathname|useSearchParams)\s*\(",
)
# Strong screen signal: imperative navigation calls.  Matches well-known
# identifiers (``router``, ``history``) AND any identifier that has ``nav``/
# ``Nav`` as a token (``navigation``, ``navigator``, ``navigationServices``,
# ``navService``, ``appNav``, ``myNavigator``, …).  Covers custom navigation
# wrappers without hard-coding individual names.
_RE_SCREEN_NAV_CALL = re.compile(
    r"\b(?:router|history|\w*(?:[Nn]avig\w*|[Nn]av[A-Z]\w*|[Nn]av))\s*\.\s*"
    r"(?:navigate|push|goBack|replace|reset|pop|dispatch|redirect)\s*\(",
)
# Weak screen signal: function receives React-Navigation props by name
_RE_SCREEN_PROP_NAMES = re.compile(
    r"[({,]\s*(?:navigation|route)\s*[,)}\s:]",
)


# ─── 2. Middleware / backend-interaction detection ────────────────────────────

_RE_MIDDLEWARE_API = re.compile(
    r"\b(?:fetch|axios|got|ky|superagent|request|createApi|buildFetcher|"
    r"XMLHttpRequest)\s*[\.(\"'`]",
    re.IGNORECASE,
)
_RE_MIDDLEWARE_QUERY = re.compile(
    r"\b(?:useQuery|useMutation|useInfiniteQuery|useSWR|useApolloQuery|"
    r"useLazyQuery|gql|graphql|createAsyncThunk)\s*[\.(]",
    re.IGNORECASE,
)
_RE_MIDDLEWARE_REDUX = re.compile(
    r"\b(?:createSlice|createReducer|createAction|createStore|configureStore|"
    r"applyMiddleware|useDispatch|useSelector)\s*[\.(]",
    re.IGNORECASE,
)
_RE_SERVICE_LAYER = re.compile(
    r"\b(?:prisma|knex|sequelize|mongoose|typeorm|redis|supabase|firebase|"
    r"neo4j|mongodb|pg\.|mysql)\s*[\.(]",
    re.IGNORECASE,
)


# ─── 3. API call extraction ───────────────────────────────────────────────────

# fetch("url") or fetch(`url`, { method: "POST" })
_RE_FETCH_CALL = re.compile(
    r'\bfetch\s*\(\s*(?P<url>[`\'][^`\']+[`\']|"[^"]+"|`[^`]+`)',
    re.MULTILINE,
)
_RE_FETCH_METHOD = re.compile(
    r'method\s*:\s*[\'"`](?P<method>GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)[\'"`]',
    re.IGNORECASE,
)
_RE_AXIOS_SHORTHAND = re.compile(
    r'\baxios\s*\.\s*(?P<method>get|post|put|patch|delete|head|options)\s*\(\s*'
    r'(?P<url>[`\'][^`\']+[`\']|"[^"]+"|[`][^`]+[`])',
    re.MULTILINE | re.IGNORECASE,
)
_RE_AXIOS_CONFIG = re.compile(
    r'\baxios\s*\(\s*\{[^}]*?url\s*:\s*(?P<url>[`\'][^`\']+[`\']|"[^"]+"|`[^`]+`)'
    r'[^}]*?(?:method\s*:\s*[\'"`](?P<method>[A-Z]+)[\'"`])?',
    re.MULTILINE | re.DOTALL,
)
_RE_HTTP_CLIENT = re.compile(
    r'\bhttp\s*\.\s*(?P<method>get|post|put|patch|delete)\s*(?:<[^>]*>)?\s*\(\s*'
    r'(?P<url>[`\'][^`\']+[`\']|"[^"]+"|`[^`]+`)',
    re.MULTILINE | re.IGNORECASE,
)
_RE_NAMED_CLIENT = re.compile(
    r'\b(?P<client>api|client|http|request|service|instance)\s*\.\s*'
    r'(?P<method>get|post|put|patch|delete)\s*(?:<[^>]*>)?\s*\(\s*'
    r'(?P<url>[`\'][^`\']+[`\']|"[^"]+"|`[^`]+`)',
    re.MULTILINE | re.IGNORECASE,
)
_RE_AXIOS_CREATE = re.compile(
    r'\baxios\.create\s*\(\s*\{[^}]*?baseURL\s*:\s*(?P<base>[`\'""][^`\'""][`\'""]|[`\'"]+[^`\'"]+[`\'"]+)',
    re.MULTILINE,
)
_RE_ENV_VAR = re.compile(r'process\.env\.[A-Z_]+')


# ─── 4. Navigation call detection ────────────────────────────────────────────

# Pass-1 patterns: hook / prop assignment detection.
# Hook names are generalised so custom wrappers like ``useAppNavigation``,
# ``useMyNavigation``, ``useTypedNavigation`` are detected in addition to the
# canonical ``useNavigation`` / ``useNavigate`` from react-navigation /
# react-router.
_RE_ASSIGN_USE_NAVIGATION = re.compile(
    r'\bconst\s+(?P<var>[a-zA-Z_]\w+)\s*=\s*use\w*Navigation\s*\(',
    re.MULTILINE,
)
_RE_ASSIGN_USE_NAVIGATION_DESTRUCT = re.compile(
    r'\bconst\s+\{[^}]{0,120}\bnavigate\b[^}]{0,120}\}\s*=\s*use\w*Navigation\s*\(',
    re.MULTILINE,
)
_RE_ASSIGN_USE_ROUTER = re.compile(
    r'\bconst\s+(?P<var>[a-zA-Z_]\w+)\s*=\s*use\w*Router\s*\(',
    re.MULTILINE,
)
_RE_ASSIGN_USE_NAVIGATE = re.compile(
    r'\bconst\s+(?P<var>[a-zA-Z_]\w+)\s*=\s*use\w*Navigate\s*\(',
    re.MULTILINE,
)
_RE_ASSIGN_USE_HISTORY = re.compile(
    r'\bconst\s+(?P<var>[a-zA-Z_]\w+)\s*=\s*useHistory\s*\(',
    re.MULTILINE,
)

# Pass-2 patterns: always-on (well-known names)
_RE_NAV_PROP_CALL = re.compile(
    r'\b(?:navigation|navigator)\s*(?:\.current\s*\??\s*)?\.'
    r'(?P<method>navigate|push|replace|reset|goTo)\s*\(\s*'
    r'[\'"`](?P<target>[A-Za-z0-9_./: -]+)[\'"`]',
    re.MULTILINE,
)
_RE_NAV_PROP_OBJ = re.compile(
    r'\b(?:navigation|navigator)\s*\.'
    r'(?:navigate|push|reset)\s*\(\s*'
    r'{\s*(?:pathname|name|routeName|screen)\s*:\s*[\'"`](?P<target>[A-Za-z0-9_./: -]+)[\'"`]',
    re.MULTILINE,
)
_RE_ROUTER_CALL = re.compile(
    r'\b(?P<var>router|history)\s*\.'
    r'(?P<method>navigate|push|replace|redirect)\s*\(\s*'
    r'[\'"`](?P<target>[A-Za-z0-9_./: -]+)[\'"`]',
    re.MULTILINE,
)
_RE_ROUTER_OBJ = re.compile(
    r'\b(?P<var>router|history)\s*\.'
    r'(?P<method>navigate|push|replace)\s*\(\s*'
    r'{\s*pathname\s*:\s*[\'"`](?P<target>[A-Za-z0-9_./: -]+)[\'"`]',
    re.MULTILINE,
)
_RE_NAV_REF_CALL = re.compile(
    r'\b\w*[Nn]av(?:igation)?[Rr]ef\b'
    r'(?:[^;(]{0,40}\.current\s*\??\s*)?'
    r'\s*\.\s*(?P<method>navigate|push|replace)\s*\(\s*'
    r'[\'"`](?P<target>[A-Za-z0-9_./: -]+)[\'"`]',
    re.MULTILINE,
)

# Generic navigation-service / wrapper object call.  Matches any identifier
# that contains ``nav``/``Nav`` as a token — examples: ``navigationServices``,
# ``navService``, ``navRef``, ``appNav``, ``rootNav``, ``myNavigator``.  The
# three alternatives cover full words (``navigator``/``navigation…``),
# camelCase tokens (``navService``/``NavRef``), and bare ``nav``.
_RE_NAV_SERVICE_CALL = re.compile(
    r'\b(?P<var>\w*(?:[Nn]avig\w*|[Nn]av[A-Z]\w*|[Nn]av))\s*'
    r'(?:\.current\s*\??\s*)?\.\s*'
    r'(?P<method>navigate|push|replace|reset|goTo)\s*\(\s*'
    r'[\'"`](?P<target>[A-Za-z0-9_./: -]+)[\'"`]',
    re.MULTILINE,
)
# Object-form target: ``navigationServices.navigate({ screen: 'X' })`` /
# ``appNav.push({ name: 'X' })``.
_RE_NAV_SERVICE_OBJ = re.compile(
    r'\b(?P<var>\w*(?:[Nn]avig\w*|[Nn]av[A-Z]\w*|[Nn]av))\s*\.\s*'
    r'(?:navigate|push|reset)\s*\(\s*'
    r'\{\s*(?:pathname|name|routeName|screen)\s*:\s*'
    r'[\'"`](?P<target>[A-Za-z0-9_./: -]+)[\'"`]',
    re.MULTILINE,
)
_RE_JSX_LINK = re.compile(
    r'<(?:Link|NavLink)\b[^>]{0,300}?\b(?:href|to)\s*=\s*'
    r'(?:[\'"](?P<route>/[^"\'>{]+)[\'"]'
    r'|{\s*[\'"`](?P<route2>/[^"\'`>{]+)[\'"`]\s*})',
    re.MULTILINE | re.DOTALL,
)
_RE_JSX_NAVIGATE_EL = re.compile(
    r'<(?:Navigate|Redirect)\b[^>]{0,200}?\bto\s*=\s*'
    r'(?:[\'"](?P<route>[^"\'>{]+)[\'"]'
    r'|{\s*[\'"`](?P<route2>[^"\'>`{]+)[\'"`]\s*})',
    re.MULTILINE | re.DOTALL,
)


@functools.lru_cache(maxsize=64)
def _nav_obj_method_re(var: str) -> re.Pattern:
    """Cached regex: ``var.navigate/push/replace/reset('Target')``."""
    return re.compile(
        r'\b' + re.escape(var) + r'\s*\.'
        r'(?P<method>navigate|push|replace|reset|goTo)\s*\(\s*'
        r'[\'"`](?P<target>[A-Za-z0-9_./: -]+)[\'"`]',
        re.MULTILINE,
    )


@functools.lru_cache(maxsize=64)
def _nav_fn_call_re(var: str) -> re.Pattern:
    """Cached regex: ``var('/path')`` or ``var('Screen')``."""
    return re.compile(
        r'\b' + re.escape(var) + r'\s*\(\s*'
        r'[\'"`](?P<target>[A-Za-z0-9_./: -]+)[\'"`]',
        re.MULTILINE,
    )


# ─── 5. Navigation Intelligence V2.0 ─────────────────────────────────────────

_RE_USER_TRIGGER = re.compile(
    r'\b(?:onClick|onPress|onTap|onSubmit|onConfirm|onLongPress|'
    r'handlePress|handleClick|handleSubmit|handleTap|onSelectItem)\b',
    re.MULTILINE,
)
_RE_SYSTEM_TRIGGER = re.compile(
    r'\b(?:useEffect|componentDidMount|componentDidUpdate|'
    r'useLayoutEffect|useMemo|useCallback)\s*\(',
    re.MULTILINE,
)
_RE_ASYNC_TRIGGER = re.compile(
    r'(?:\.then\s*\(|await\s+\w|\.\s*catch\s*\()',
    re.MULTILINE,
)
_RE_AUTH_GUARD = re.compile(
    r'\b(?:isAuth(?:enticated)?|isLoggedIn|token\b|user\.id|requiresAuth|userLoggedIn)\b',
    re.MULTILINE,
)
_RE_PERM_GUARD = re.compile(
    r'\b(?:hasPermission|canAccess|role\s*===|isAdmin|isOwner|checkPermission)\b',
    re.MULTILINE,
)

# Route config extraction
_RE_SCREEN_ELEM_START = re.compile(r'<(?:\w+\.)?Screen\b', re.MULTILINE)
_RE_SCREEN_NAME_ATTR = re.compile(r"\bname\s*=\s*['\"](?P<name>[^'\"]{1,80})['\"]")
_RE_SCREEN_COMP_ATTR = re.compile(r'\bcomponent\s*=\s*\{(?P<comp>\w+)\}')


# ─── 6. Navigator factory + ParamList detection ───────────────────────────────

_RE_NAVIGATOR_FACTORY = re.compile(
    r'(?:const|let|var)\s+'
    r'(?P<var_name>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*'
    r'(?P<factory>create(?:Stack|BottomTab|Drawer|NativeStack|MaterialTopTab)Navigator)'
    r'(?:<\s*(?P<generic>[A-Za-z_$][A-Za-z0-9_$<>, ]*?)\s*>)?'
    r'\s*\(\s*\)',
    re.MULTILINE,
)

_FACTORY_TO_NAV_TYPE: Dict[str, str] = {
    "createStackNavigator":          "stack",
    "createNativeStackNavigator":    "native_stack",
    "createBottomTabNavigator":      "tab",
    "createDrawerNavigator":         "drawer",
    "createMaterialTopTabNavigator": "material_top",
}


# ─── 7. Function-kind map for call_expression initializers ────────────────────

_CALL_EXPR_KIND_MAP: Dict[str, str] = {
    # ── Redux Toolkit ──
    "createAsyncThunk":       "thunk",
    "createSlice":            "redux_slice",
    "createAction":           "action_creator",
    "createReducer":          "reducer",
    "createSelector":         "selector",
    "createApi":              "api_service",
    "createEntityAdapter":    "entity_adapter",
    "createListenerMiddleware": "middleware",
    # ── React wrappers / HOCs ──
    "memo":                   "component",
    "forwardRef":             "component",
    "lazy":                   "component",
    "connect":                "hoc_connected",
    "compose":                "hoc_composed",
    "pipe":                   "hoc_composed",
    "createContext":          "context",
    "styled":                 "styled_component",
    # ── Vue / Nuxt ──
    "defineComponent":        "component",
    "defineAsyncComponent":   "component",
    "defineCustomElement":    "component",
    "defineStore":            "store",
    "defineNuxtConfig":       "config",
    "defineNuxtPlugin":       "plugin",
    "defineNuxtRouteMiddleware": "middleware",
    "defineEventHandler":     "handler",
    "definePage":             "page",
    "definePageMeta":         "page_meta",
    # ── State management (non-Redux) ──
    "createStore":            "store",
    "atom":                   "atom",
    "createMachine":          "state_machine",
    "createModel":            "model",
    "makeAutoObservable":     "observable",
    "observable":             "observable",
    # ── Server / routing / middleware ──
    "createServer":           "server",
    "createApp":              "app",
    "createRouter":           "router",
    "createTRPCRouter":       "router",
    "createCallerFactory":    "factory",
    "createMiddleware":       "middleware",
    "createClient":           "client",
    "createTRPCProxyClient":  "client",
    "initTRPC":               "trpc_init",
    "initTRPC.create":        "trpc_init",
    # ── Configuration / build ──
    "defineConfig":           "config",
    # ── Styling ──
    "makeStyles":             "styles",
    "createStyles":           "styles",
    "createTheme":            "theme",
    # ── Testing ──
    "createMock":             "mock",
    "createStub":             "mock",
    # ── Angular ──
    "inject":                 "injection",
    # ── Generic ──
    "create":                 "function_variable",
}
