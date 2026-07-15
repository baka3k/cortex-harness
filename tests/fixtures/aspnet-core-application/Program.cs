var builder = WebApplication.CreateBuilder(args);
builder.Services.AddScoped<GreetingService>();
builder.Services.AddControllers();

var app = builder.Build();
app.UseExceptionHandler("/error");
app.UseAuthentication();
app.UseAuthorization();
app.MapGet("/hello/{name}", (string name, GreetingService service) => Results.Ok(service.Greet(name)));
app.MapControllers();
app.Run();

public sealed class GreetingService
{
    public string Greet(string name) => $"Hello {name}";
}
