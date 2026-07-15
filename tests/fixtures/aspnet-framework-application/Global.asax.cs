using System.Web;

namespace LegacyWeb
{
    public class MvcApplication : HttpApplication
    {
        protected void Application_Start() { }
        protected void Application_BeginRequest() { }
        protected void Application_Error() { }
    }

    public sealed class AuditModule : IHttpModule
    {
        public void Init(HttpApplication context) { }
        public void Dispose() { }
    }
}
