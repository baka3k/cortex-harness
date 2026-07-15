using System.Web.Mvc;

namespace LegacyWeb.Controllers
{
    public class HomeController : Controller
    {
        [HttpGet]
        public ActionResult Index() { return View(); }
    }
}
