using System;
using System.Web.UI;

namespace LegacyWeb
{
    public class DefaultPage : Page
    {
        protected void SaveButton_Click(object sender, EventArgs args)
        {
            Session["last-action"] = "save";
        }
    }
}
