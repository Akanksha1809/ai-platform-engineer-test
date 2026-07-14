output "ecs_cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "ecs_service_name" {
  value = aws_ecs_service.this.name
}

output "s3_bucket_name" {
  value = aws_s3_bucket.this.bucket
}

output "db_endpoint" {
  value = aws_db_instance.this.endpoint
}
