import os

class Config:
    SECRET_KEY = os.urandom(24)
    DATABASE_URL = os.getenv('SUPABASE_DATABASE_URL', 'postgresql://postgres.kcsxtvbepmdgohpnggdd:ZnyDyoH68uA3Rp87@aws-0-us-west-1.pooler.supabase.com:5432/postgres?sslmode=require')
    LOGIN_DATABASE_URL = os.getenv('LOGIN_DATABASE_URL', 'postgresql://postgres.kcsxtvbepmdgohpnggdd:ZnyDyoH68uA3Rp87@aws-0-us-west-1.pooler.supabase.com:5432/postgres?sslmode=require')
