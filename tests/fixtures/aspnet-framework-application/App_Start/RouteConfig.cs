using System.Web.Routing;

namespace LegacyWeb
{
    public static class RouteConfig
    {
        public static void RegisterRoutes(RouteCollection routes)
        {
            routes.MapPageRoute("legacy", "legacy/{id}", "~/Default.aspx");
        }
    }
}
