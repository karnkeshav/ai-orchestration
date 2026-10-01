terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
  }

  # bucket / key / region are passed by the pipeline with -backend-config
  backend "s3" {}
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project    = var.name
      ManagedBy  = "ai-orchestration-studio"
      TTLMinutes = tostring(var.ttl_minutes)
    }
  }
}

locals {
  # Graviton families (t4g, m7g, c7g, ...) need the arm64 AMI
  arch      = can(regex("^[a-z][0-9]+g[a-z]*\\.", var.instance_type)) ? "arm64" : "x86_64"
  ami_param = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-${local.arch}"
}

data "aws_ssm_parameter" "ami" {
  name = local.ami_param
}

# Pick an AZ that actually offers the requested instance type
data "aws_ec2_instance_type_offerings" "supported" {
  location_type = "availability-zone"

  filter {
    name   = "instance-type"
    values = [var.instance_type]
  }
}

resource "aws_vpc" "main" {
  cidr_block           = "10.42.0.0/16"
  enable_dns_hostnames = true

  tags = {
    Name = "${var.name}-vpc"
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${var.name}-igw"
  }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.42.1.0/24"
  availability_zone       = sort(data.aws_ec2_instance_type_offerings.supported.locations)[0]
  map_public_ip_on_launch = true

  tags = {
    Name = "${var.name}-public"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name = "${var.name}-rt"
  }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "web" {
  name        = "${var.name}-web"
  description = "HTTP only, no SSH"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_instance" "web" {
  ami                         = data.aws_ssm_parameter.ami.value
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.public.id
  vpc_security_group_ids      = [aws_security_group.web.id]
  user_data_replace_on_change = true
  user_data                   = templatefile("${path.module}/web/user_data.sh.tftpl", { index_b64 = filebase64("${path.module}/web/index.html") })

  metadata_options {
    http_tokens = "required"
  }

  root_block_device {
    volume_type = "gp3"
    encrypted   = true
  }

  tags = {
    Name = "${var.name}-web"
  }
}
