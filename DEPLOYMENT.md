# Deployment Guide for Language Learning App

## Render.com Deployment

This guide will help you deploy your Flask language learning application to Render.com.

### Prerequisites

1. A Render.com account (free tier available)
2. Your code pushed to a Git repository (GitHub, GitLab, or Bitbucket)
3. Required API keys and credentials

### Environment Variables Required

Set these environment variables in your Render service:

```
SECRET_KEY=your-super-secret-key-here
GEMINI_API_KEY=your-gemini-api-key-here
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=465
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
DEBUG=false
```

### Deployment Steps

1. **Push your code to a Git repository**
2. **Create a new Web Service on Render**
3. **Configure the service settings**
4. **Set environment variables**
5. **Deploy and test**

### Important Notes

- Your database file (database.db) will be included in the deployment
- For production, consider upgrading to Render's PostgreSQL for better persistence
- The app will automatically create necessary directories on startup
- File uploads are stored in `static/uploads/` directory

### Security Considerations

- Never commit your `.env` file with real credentials
- Use strong, unique secret keys
- Consider using Render's PostgreSQL for production database
- Enable HTTPS (automatically provided by Render)

### Troubleshooting

- Check Render logs if deployment fails
- Ensure all environment variables are set correctly
- Verify that your Git repository is accessible to Render 