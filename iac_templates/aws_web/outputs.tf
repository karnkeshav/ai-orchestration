output "url" {
  value = "http://${aws_instance.web.public_ip}"
}

output "instance_id" {
  value = aws_instance.web.id
}

output "instance_type" {
  value = aws_instance.web.instance_type
}

output "availability_zone" {
  value = aws_instance.web.availability_zone
}
