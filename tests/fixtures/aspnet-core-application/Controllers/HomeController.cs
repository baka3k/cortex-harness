using Microsoft.AspNetCore.Mvc;

namespace CoreWeb.Controllers;

[ApiController]
[Route("api/[controller]")]
public sealed class HomeController : ControllerBase
{
    [HttpGet("status")]
    public IActionResult Status() => Ok(new { ready = true });
}
